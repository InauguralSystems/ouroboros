#include "aot_rt.h"
#include "ems_diagnostics_metrics.h"

static unsigned long long before[EMSD_COUNT], after[EMSD_COUNT];
static int failures;
#define SNAP_BEFORE() emsd_snapshot(before)
#define SNAP_AFTER() emsd_snapshot(after)
#define DELTA(m) (after[EMSD_##m] - before[EMSD_##m])
#define CHECK(cond, label) do { if (!(cond)) { fprintf(stderr, "CONTROL_FAIL %s\n", label); failures++; } } while (0)

int main(void) {
    Env *env = aot_boot();
    CHECK(!g_arena.active, "boot arena inactive");
    SNAP_BEFORE();
    Value *number = make_num(12345);
    SNAP_AFTER();
    CHECK(DELTA(num_requests) == 1, "one numeric request");
    CHECK(DELTA(num_arena_allocations) == 0, "no arena allocation");
    CHECK(DELTA(num_heap_allocations) + DELTA(num_freelist_reuses) == 1, "numeric request partition");
    val_decref(number);
    SNAP_BEFORE();
    number = make_num(12346);
    SNAP_AFTER();
    CHECK(DELTA(num_requests) == 1 && DELTA(num_freelist_reuses) == 1 && DELTA(num_heap_allocations) == 0, "returned number reused");
    val_decref(number);
    SNAP_BEFORE();
    number = make_num_permanent(12347);
    SNAP_AFTER();
    CHECK(DELTA(num_permanent_requests) == 1 && DELTA(num_requests) == 0, "permanent allocation separate");
    val_decref(number);

    SNAP_BEFORE();
    Value *list = make_list(0);
    for (int i = 0; i < 9; ++i) list_append_owned(list, make_null());
    SNAP_AFTER();
    CHECK(DELTA(list_creates) == 1 && DELTA(list_initial_slots) == 8, "list initial capacity");
    CHECK(DELTA(list_append_requests) == 9 && DELTA(list_append_successes) == 9, "append wrapper counted once");
    CHECK(DELTA(list_heap_reallocations) == 1 && DELTA(list_added_slots) == 8, "list growth");
    CHECK(DELTA(num_requests) == 0, "null appends allocate no nums");
    val_decref(list);
    SNAP_BEFORE();
    list = make_list_heap(13);
    SNAP_AFTER();
    CHECK(DELTA(list_heap_forced_creates) == 1 && DELTA(list_creates) == 0 && DELTA(list_initial_slots) == 13, "heap forced capacity");
    val_decref(list);
    SNAP_BEFORE();
    list_append(NULL, make_null());
    SNAP_AFTER();
    CHECK(DELTA(list_append_requests) == 1 && DELTA(list_append_successes) == 0, "invalid append request");

    Value *fn = env_get(env, "len");
    CHECK(fn && fn->type == VAL_BUILTIN, "registered len builtin");
    if (!fn || fn->type != VAL_BUILTIN) return 1;
    val_incref(fn);
    env_set_local(env, "ems_control_len_alias_one", fn);
    env_set_local(env, "ems_control_len_alias_two", fn);
    SNAP_BEFORE();
    for (int i = 0; i < 2; ++i) {
        val_incref(fn);
        Value *result = aot_call_dispatch(fn, make_list(0));
        CHECK(result && result->type == VAL_NUM && result->data.num == 0, "actual len behavior");
        val_decref(result);
    }
    SNAP_AFTER();
    CHECK(DELTA(dispatch_builtin_calls) == 2 && DELTA(dispatch_function_calls) == 0, "two builtin dispatches");
    val_decref(fn);

    gc_collect_cycles();
    Value *live = make_list(0);
    list_append(live, make_null());
    SNAP_BEFORE();
    Value *cycle = make_list(0);
    list_append(cycle, cycle);
    val_decref(cycle);
    gc_collect_cycles();
    SNAP_AFTER();
    CHECK(DELTA(gc_collections) >= 1 && DELTA(gc_reclaimed_nodes) >= 1, "cycle collection witness");
    CHECK(live->type == VAL_LIST && live->data.list.count == 1, "live collection witness");
    val_decref(live);

    emsd_snapshot(after);
    CHECK(after[EMSD_num_requests] == after[EMSD_num_heap_allocations] + after[EMSD_num_arena_allocations] + after[EMSD_num_freelist_reuses], "whole numeric partition");
    CHECK(after[EMSD_gc_collections] == after[EMSD_gc_completed_collections] + after[EMSD_gc_accounting_aborts], "whole GC partition");
    printf("DIAGNOSTICS_CONTROLS failures=%d\n", failures);
    return failures ? 1 : 0;
}
