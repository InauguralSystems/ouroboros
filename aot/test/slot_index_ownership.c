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
        assert(strcmp(list_get_ref(result, 0)->data.str, "retained") == 0);

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
        assert(slot_is_num(replaced) && aot_slot_num_proven(replaced) == k);
        index_i(&numbers, numbers, 0);
        assert(slot_is_num(numbers) && aot_slot_num_proven(numbers) == k);
        slot_decref(replaced);
        slot_decref(numbers);
    }
}

static void check_number_boundaries(void) {
    /* (#1665 arm D, EigenScript v0.46.0) A list element is a slot, and every
     * stored number is num_guard'ed on the way in (container.h), so the old
     * unguarded heap-number element (an INFINITY written through the box) can
     * no longer be built. What remains of this boundary: the guarded
     * immediate is read back raw, with no second guard and no flags. */
    EigsSlot numbers = one_item(make_num(INFINITY));   /* stored as EIGS_NUM_MAX */
    EigsSlot result = slot_null();
    g_math_flags = 0;
    index_i(&result, numbers, 0);
    assert(slot_is_num(result) && aot_slot_num_proven(result) == EIGS_NUM_MAX);
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
    assert(slot_is_num(result) && aot_slot_num_proven(result) == EIGS_NUM_MAX);
    assert(g_math_flags == EIGS_MATH_OVERFLOW);
    buffer->data.buffer.data[0] = NAN;
    g_math_flags = 0;
    {
        /* Zero/INVALID is the soft-mode scalar contract, not the boot default. */
        int saved_strict = g_strict;
        g_strict = 0;
        index_v(&result, values, make_num(0));
        assert(slot_is_num(result) && aot_slot_num_proven(result) == 0);
        assert(g_math_flags == EIGS_MATH_INVALID);
        g_strict = saved_strict;
    }
    slot_decref(values);
    slot_decref(result);
}

/* check_arena_elements was removed with the bump arena (EigenScript v0.46.0,
 * #1665): there are no arena values to promote. */

static int scalar_index_eq(EigsSlot table, EigsSlot index, EigsSlot rhs) {
#ifdef SLOT_INDEX_BASELINE
    Value *left = aot_index_get(slot_to_value(table), slot_to_value(index));
    Value *right = slot_to_value(rhs);
    return aot_truthy(aot_eq(left, right));
#else
    AotScalarRead left = aot_scalar_index(table, aot_scalar_slot(index));
    AotScalarRead right = aot_scalar_slot(rhs);
    return aot_scalar_equal(left, right, "==");
#endif
}
static int scalar_index_eq_num(EigsSlot table, EigsSlot index, double rhs) {
#ifdef SLOT_INDEX_BASELINE
    return aot_eq_n_t(aot_index_get(slot_to_value(table), slot_to_value(index)), rhs);
#else
    AotScalarRead left = aot_scalar_index(table, aot_scalar_slot(index));
    return aot_scalar_equal(left, aot_scalar_number(rhs), "==");
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
    return aot_scalar_equal(aot_scalar_add_slots(left, right), aot_scalar_number(expected), "==");
#endif
}
static void check_scalar_consumers(void) {
    g_math_flags = 0;
    assert(scalar_sum_eq_num(slot_from_num(EIGS_NUM_MAX), slot_from_num(EIGS_NUM_MAX), EIGS_NUM_MAX));
    assert(g_math_flags == EIGS_MATH_OVERFLOW);
    g_math_flags = 0;
    assert(scalar_sum_eq_num(slot_from_num(-EIGS_NUM_MAX), slot_from_num(-EIGS_NUM_MAX), -EIGS_NUM_MAX));
    assert(g_math_flags == EIGS_MATH_OVERFLOW);
    /* (#1665 arm D) the heap INFINITY / heap-NaN element cases are gone with
     * slot storage: a stored element is a guarded immediate (never inf or
     * NaN). The guarded maximum still reads back raw, without flags. */
    EigsSlot numbers = one_item(make_num(INFINITY));   /* stored as EIGS_NUM_MAX */
    g_math_flags = 0;
    assert(scalar_index_eq_num(numbers, slot_from_num(0), EIGS_NUM_MAX));
    assert(g_math_flags == 0); /* list read is raw: no second guard */
    slot_decref(numbers);
    numbers = one_item(make_num(17));
    g_math_flags = 0;
    {
        /* Materializing the NaN index as zero explicitly requires soft mode. */
        int saved_strict = g_strict;
        g_strict = 0;
        assert(scalar_index_eq_num(numbers, slot_from_num(NAN), 17));
        assert(g_math_flags == EIGS_MATH_INVALID); /* immediate index materializes */
        g_strict = saved_strict;
    }
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
    {
        /* Match the soft-mode buffer-read contract in both helper arms. */
        int saved_strict = g_strict;
        g_strict = 0;
        assert(scalar_index_eq_num(values, slot_from_num(0), 0));
        assert(g_math_flags == EIGS_MATH_INVALID);
        g_strict = saved_strict;
    }
    slot_decref(values);

    for (int k = 0; k < 300; ++k) {
        EigsSlot child = one_item(make_str("scalar retained"));
        EigsSlot parent = one_item(slot_as_ptr(child));
        EigsSlot right = one_item(make_str("scalar retained"));
        assert(scalar_index_eq(parent, slot_from_num(0), right));
        slot_decref(right);
        scalar_sum_store(&parent, parent, slot_from_num(0), slot_from_num(0));
        assert(strcmp(list_get_ref(slot_as_ptr(parent), 0)->data.str, "scalar retained") == 0);
        slot_decref(parent);
    }
}

int main(void) {
    Env *env = aot_boot();
    check_aliases();
    check_number_boundaries();
    check_scalar_consumers();
    puts("slot index: 300 alias cycles; heap/buffer boundaries OK; scalar consumers OK");
    /* LSan's nonzero exit can bypass the normal stdio flush. */
    fflush(stdout);
    aot_shutdown(env);
    return 0;
}
