/* Direct ownership/representation companion to t337--t341.
 * Compile with SLOT_INDEX_BASELINE and the old aot_rt.h for the A/B control;
 * both arms call the original consuming helpers on their fallback paths.
 * Run both with ASan. No shutdown leak allowance is assumed by this file.
 */
#include "aot_rt.h"
#include <assert.h>

#ifdef SLOT_INDEX_BASELINE
static void index_i(EigsSlot *dst, EigsSlot target, double index) {
    aot_lv_set(dst, aot_index_get_i(slot_to_value(target), index));
}
static void index_s(EigsSlot *dst, EigsSlot target, EigsSlot index) {
    Value *t = slot_to_value(target);
    Value *i = slot_to_value(index);
    aot_lv_set(dst, aot_index_get(t, i));
}
static void index_v(EigsSlot *dst, EigsSlot target, Value *index) {
    aot_lv_set(dst, aot_index_get(slot_to_value(target), index));
}
#else
#define index_i aot_lv_index_i
#define index_s aot_lv_index_s
#define index_v aot_lv_index_v
#endif

static EigsSlot one_item(Value *owned) {
    Value *list = make_list(1);
    list_append_owned(list, owned);
    return slot_from_value(list);
}

static void check_aliases(void) {
    for (int k = 0; k < 300; ++k) {
        EigsSlot child = one_item(make_str("retained"));
        /* Transfer child into a new parent: parent is its only owner. */
        EigsSlot parent = one_item(slot_as_ptr(child));
        index_i(&parent, parent, 0);
        Value *result = slot_as_ptr(parent);
        assert(result->type == VAL_LIST && result->data.list.count == 1);
        assert(strcmp(result->data.list.items[0]->data.str, "retained") == 0);

        EigsSlot index = slot_from_heap(make_num(0));
        /* Destination aliases a heap-number index, then contains a pointer. */
        index_s(&index, parent, index);
        assert(slot_is_ptr(index));
        assert(strcmp(slot_as_ptr(index)->data.str, "retained") == 0);
        slot_decref(parent);
        assert(strcmp(slot_as_ptr(index)->data.str, "retained") == 0);
        slot_decref(index);

        EigsSlot numbers = one_item(make_num(k));
        EigsSlot replaced = one_item(make_str("old destination"));
        index_v(&replaced, numbers, make_num(0));
        assert(slot_is_num(replaced) && replaced.d == k);
        index_i(&numbers, numbers, 0);
        assert(slot_is_num(numbers) && numbers.d == k);
        slot_decref(replaced);
        slot_decref(numbers);
    }
}

static void check_number_boundaries(void) {
    EigsSlot numbers = one_item(make_num(0));
    /* Raw runtime storage: the old list-read + slot_from_value does not
     * guard a heap element. An added guard would change both value and flags. */
    slot_as_ptr(numbers)->data.list.items[0]->data.num = INFINITY;
    EigsSlot result = slot_null();
    g_math_flags = 0;
    index_i(&result, numbers, 0);
    assert(slot_is_num(result) && isinf(result.d));
    assert(g_math_flags == 0);
    slot_decref(numbers);

    Value *buffer = xcalloc(1, sizeof(Value));
    buffer->type = VAL_BUFFER;
    buffer->refcount = 1;
    buffer->data.buffer.count = 1;
    buffer->data.buffer.data = xcalloc(1, sizeof(double));
    EigsSlot values = slot_from_value(buffer);
    buffer->data.buffer.data[0] = INFINITY;
    g_math_flags = 0;
    index_s(&result, values, slot_from_num(0));
    assert(slot_is_num(result) && result.d == EIGS_NUM_MAX);
    assert(g_math_flags == EIGS_MATH_OVERFLOW);
    buffer->data.buffer.data[0] = NAN;
    g_math_flags = 0;
    index_v(&result, values, make_num(0));
    assert(slot_is_num(result) && result.d == 0);
    assert(g_math_flags == EIGS_MATH_INVALID);
    slot_decref(values);
    slot_decref(result);
}

static void check_arena_elements(void) {
    /* Construct the runtime's borrowed arena element boundary directly.
     * Language list writes normally promote before this read can see it. */
    Value *list = make_list(1);
    list->data.list.count = 1;
    EigsSlot target = slot_from_value(list);
    EigsSlot result = slot_null();
    arena_mark_pos();
    list->data.list.items[0] = make_str("arena element");
    assert(list->data.list.items[0]->arena);
    index_i(&result, target, 0);
    assert(slot_is_ptr(result) && !slot_as_ptr(result)->arena);
    slot_decref(target);
    arena_reset_to_mark();
    arena_mark_pos();
    (void)make_str("overwrite the released arena element");
    assert(strcmp(slot_as_ptr(result)->data.str, "arena element") == 0);
    arena_reset_to_mark();
    slot_decref(result);

    list = make_list(1);
    list->data.list.count = 1;
    target = slot_from_value(list);
    result = slot_null();
    arena_mark_pos();
    list->data.list.items[0] = make_num(0);
    list->data.list.items[0]->data.num = INFINITY;
    g_math_flags = 0;
    index_i(&result, target, 0);
    /* Unlike a heap number, the old arena promotion calls num_guard. */
    assert(slot_is_num(result) && result.d == EIGS_NUM_MAX);
    assert(g_math_flags == EIGS_MATH_OVERFLOW);
    slot_decref(target);
    arena_reset_to_mark();
    slot_decref(result);
}

static int scalar_index_eq(EigsSlot table, EigsSlot index, EigsSlot rhs) {
#ifdef SLOT_INDEX_BASELINE
    Value *left = aot_index_get(slot_to_value(table), slot_to_value(index));
    Value *right = slot_to_value(rhs);
    return aot_truthy(aot_eq(left, right));
#else
    AotScalarRead left = aot_scalar_index(table, aot_scalar_slot(index));
    AotScalarRead right = aot_scalar_slot(rhs);
    return aot_scalar_equal(left, right);
#endif
}
static int scalar_index_eq_num(EigsSlot table, EigsSlot index, double rhs) {
#ifdef SLOT_INDEX_BASELINE
    return aot_eq_n_t(aot_index_get(slot_to_value(table), slot_to_value(index)), rhs);
#else
    AotScalarRead left = aot_scalar_index(table, aot_scalar_slot(index));
    return aot_scalar_equal(left, aot_scalar_number(rhs));
#endif
}
static void scalar_sum_store(EigsSlot *dst, EigsSlot table, EigsSlot left, EigsSlot right) {
#ifdef SLOT_INDEX_BASELINE
    Value *l = slot_to_value(left);
    Value *r = slot_to_value(right);
    aot_lv_index_v(dst, table, aot_add(l, r));
#else
    aot_lv_index_add_slots(dst, table, left, right);
#endif
}
static int scalar_sum_eq_num(EigsSlot left, EigsSlot right, double expected) {
#ifdef SLOT_INDEX_BASELINE
    Value *l = slot_to_value(left);
    Value *r = slot_to_value(right);
    return aot_eq_n_t(aot_add(l, r), expected);
#else
    return aot_scalar_equal(aot_scalar_add_slots(left, right), aot_scalar_number(expected));
#endif
}
static void check_scalar_consumers(void) {
    g_math_flags = 0;
    assert(scalar_sum_eq_num(slot_from_num(EIGS_NUM_MAX), slot_from_num(EIGS_NUM_MAX), EIGS_NUM_MAX));
    assert(g_math_flags == EIGS_MATH_OVERFLOW);
    g_math_flags = 0;
    assert(scalar_sum_eq_num(slot_from_num(-EIGS_NUM_MAX), slot_from_num(-EIGS_NUM_MAX), -EIGS_NUM_MAX));
    assert(g_math_flags == EIGS_MATH_OVERFLOW);
    EigsSlot numbers = one_item(make_num(0));
    Value *number = slot_as_ptr(numbers)->data.list.items[0];
    number->data.num = INFINITY;
    g_math_flags = 0;
    assert(scalar_index_eq_num(numbers, slot_from_num(0), INFINITY));
    assert(g_math_flags == 0); /* heap list read is raw, not stored in a slot */
    number->data.num = NAN;
    val_incref(number);
    EigsSlot same = slot_from_heap(number);
    g_math_flags = 0;
    assert(scalar_index_eq(numbers, slot_from_num(0), same));
    assert(!scalar_index_eq_num(numbers, slot_from_num(0), 0));
    assert(g_math_flags == 0); /* heap NaN identity must survive */
    slot_decref(same);
    number->data.num = 17;
    g_math_flags = 0;
    assert(scalar_index_eq_num(numbers, slot_from_num(NAN), 17));
    assert(g_math_flags == EIGS_MATH_INVALID); /* immediate index materializes */
    assert(scalar_index_eq_num(numbers, slot_from_num(-1), 17));
    slot_decref(numbers);

    Value *buffer = xcalloc(1, sizeof(Value));
    buffer->type = VAL_BUFFER;
    buffer->refcount = 1;
    buffer->data.buffer.count = 1;
    buffer->data.buffer.data = xcalloc(1, sizeof(double));
    EigsSlot values = slot_from_value(buffer);
    buffer->data.buffer.data[0] = INFINITY;
    g_math_flags = 0;
    assert(scalar_index_eq_num(values, slot_from_num(0), EIGS_NUM_MAX));
    assert(g_math_flags == EIGS_MATH_OVERFLOW);
    buffer->data.buffer.data[0] = NAN;
    g_math_flags = 0;
    assert(scalar_index_eq_num(values, slot_from_num(0), 0));
    assert(g_math_flags == EIGS_MATH_INVALID);
    slot_decref(values);

    Value *arena_list = make_list(1);
    arena_list->data.list.count = 1;
    EigsSlot arena_target = slot_from_value(arena_list);
    arena_mark_pos();
    arena_list->data.list.items[0] = make_num(0);
    arena_list->data.list.items[0]->data.num = INFINITY;
    g_math_flags = 0;
    assert(scalar_index_eq_num(arena_target, slot_from_num(0), INFINITY));
    assert(g_math_flags == 0); /* read does not promote an arena number */
    slot_decref(arena_target);
    arena_reset_to_mark();

    for (int k = 0; k < 300; ++k) {
        EigsSlot child = one_item(make_str("scalar retained"));
        EigsSlot parent = one_item(slot_as_ptr(child));
        EigsSlot right = one_item(make_str("scalar retained"));
        assert(scalar_index_eq(parent, slot_from_num(0), right));
        slot_decref(right);
        scalar_sum_store(&parent, parent, slot_from_num(0), slot_from_num(0));
        assert(strcmp(slot_as_ptr(parent)->data.list.items[0]->data.str, "scalar retained") == 0);
        slot_decref(parent);
    }
}

int main(void) {
    Env *env = aot_boot();
    check_aliases();
    check_number_boundaries();
    check_arena_elements();
    check_scalar_consumers();
    puts("slot index: 300 alias cycles; heap/buffer/arena boundaries OK; scalar consumers OK");
    /* LSan's nonzero exit can bypass the normal stdio flush. */
    fflush(stdout);
    aot_shutdown(env);
    return 0;
}
