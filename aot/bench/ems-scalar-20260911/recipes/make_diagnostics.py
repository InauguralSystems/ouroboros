#!/usr/bin/env python3
"""Emit compile-time gated runtime/AOT diagnostics copies; never edit inputs."""
import argparse
import hashlib
import json
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--runtime', type=Path, default=Path('/home/jon/src/wt/es-v043/src/eigenscript.c'))
p.add_argument('--header', type=Path, default=Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/ouroboros/aot/aot_rt.h'))
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
r = a.runtime.read_text()
h = a.header.read_text()
counts = {}

def one(text, old, new, name):
    count = text.count(old)
    assert count == 1, (name, count)
    counts[name] = count
    return text.replace(old, new)

metrics = ['num_requests', 'num_heap_allocations', 'num_arena_allocations', 'num_freelist_reuses', 'num_permanent_requests', 'list_creates', 'list_heap_forced_creates', 'list_arena_creates', 'list_initial_slots', 'list_append_requests', 'list_append_successes', 'list_heap_reallocations', 'list_arena_growth_allocations', 'list_added_slots', 'gc_requests', 'gc_collections', 'gc_scanned_universe_nodes', 'gc_accounting_aborts', 'gc_completed_collections', 'gc_reclaimed_nodes', 'dispatch_builtin_calls', 'dispatch_function_calls']
diag = '''
/* Temporary diagnostics, single-thread EMS only. Counts include shutdown. */
#ifdef EMS_DIAGNOSTICS
static unsigned long long emsd_counts[EMSD_COUNT];
void emsd_snapshot(unsigned long long *out) { memcpy(out, emsd_counts, sizeof(emsd_counts)); }
typedef struct { BuiltinFn fn; unsigned long long calls; char name[128]; } EmsdBuiltin;
static EmsdBuiltin emsd_builtins[2048];
static int emsd_builtin_count;
static EmsdBuiltin *emsd_identity(BuiltinFn fn) {
    for (int i = 0; i < emsd_builtin_count; ++i)
        if (emsd_builtins[i].fn == fn) return &emsd_builtins[i];
    if (emsd_builtin_count == 2048) { fputs("EMS_DIAGNOSTICS histogram overflow\\n", stderr); abort(); }
    EmsdBuiltin *entry = &emsd_builtins[emsd_builtin_count++];
    entry->fn = fn;
    return entry;
}
static void emsd_register_name(const char *name, Value *val) {
    if (val && val->type == VAL_BUILTIN) {
        EmsdBuiltin *entry = emsd_identity(val->data.builtin);
        if (!entry->name[0]) snprintf(entry->name, sizeof(entry->name), "%s", name);
    }
}
void emsd_dispatch(Value *fn) {
    if (fn->type == VAL_BUILTIN) {
        emsd_counts[EMSD_dispatch_builtin_calls]++;
        emsd_identity(fn->data.builtin)->calls++;
    } else if (fn->type == VAL_FN) emsd_counts[EMSD_dispatch_function_calls]++;
}
static void emsd_json_string(const char *s) {
    fputc('"', stderr);
    for (; *s; ++s) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\\\') { fputc('\\\\', stderr); fputc(c, stderr); }
        else if (c < 32 || c >= 127) fprintf(stderr, "\\\\u%04x", c);
        else fputc(c, stderr);
    }
    fputc('"', stderr);
}
static void emsd_report(void) {
    fputs("EMS_DIAGNOSTICS_JSON {\\"metrics\\":{", stderr);
REPORT_METRICS
    fputs("},\\"builtin_dispatch_by_identity\\":[", stderr);
    int emitted = 0;
    for (int i = 0; i < emsd_builtin_count; ++i) {
        EmsdBuiltin *entry = &emsd_builtins[i];
        if (!entry->calls) continue;
        if (emitted++) fputc(',', stderr);
        fprintf(stderr, "{\\"identity_index\\":%d,\\"address\\":\\"%p\\",\\"first_registered_name\\":", i, (void *)entry->fn);
        emsd_json_string(entry->name);
        fprintf(stderr, ",\\"calls\\":%llu}", entry->calls);
    }
    fputs("]}\\n", stderr);
}
__attribute__((constructor)) static void emsd_init(void) { atexit(emsd_report); }
#define EMSD_ADD(name, amount) (emsd_counts[EMSD_##name] += (unsigned long long)(amount))
#define EMSD_REGISTER(name, val) emsd_register_name(name, val)
#else
#define EMSD_ADD(name, amount) ((void)0)
#define EMSD_REGISTER(name, val) ((void)0)
#endif
'''
diag = '\nenum { ' + ', '.join('EMSD_' + m for m in metrics) + ', EMSD_COUNT };\n' + diag
diag = diag.replace('REPORT_METRICS', '\n'.join('    fprintf(stderr, "' + (',' if i else '') + '\\"' + m + '\\":%llu", emsd_counts[EMSD_' + m + ']);' for i, m in enumerate(metrics)))
r = one(r, '#include <pthread.h>', '#include <pthread.h>\n' + diag, 'diagnostic_definitions')
r = one(r, 'Value* make_num(double n) {', 'Value* make_num(double n) {\n    EMSD_ADD(num_requests, 1);', 'num_requests')
r = one(r, 'if (!from_arena && g_num_freelist) {', 'if (!from_arena && g_num_freelist) {\n        EMSD_ADD(num_freelist_reuses, 1);', 'num_reuses')
old = '''    } else {
        v = from_arena ? arena_alloc(sizeof(Value)) : xcalloc(1, sizeof(Value));
    }
    v->type = VAL_NUM;'''
new = '''    } else {
        if (from_arena) EMSD_ADD(num_arena_allocations, 1);
        else EMSD_ADD(num_heap_allocations, 1);
        v = from_arena ? arena_alloc(sizeof(Value)) : xcalloc(1, sizeof(Value));
    }
    v->type = VAL_NUM;'''
r = one(r, old, new, 'num_allocation_paths')
r = one(r, 'Value* make_num_permanent(double n) {', 'Value* make_num_permanent(double n) {\n    EMSD_ADD(num_permanent_requests, 1);', 'num_permanent')
r = one(r, 'Value* make_list(int capacity) {', 'Value* make_list(int capacity) {\n    EMSD_ADD(list_creates, 1);\n    if (g_arena.active) EMSD_ADD(list_arena_creates, 1);', 'list_creates')
r = one(r, 'Value* make_list_heap(int capacity) {', 'Value* make_list_heap(int capacity) {\n    EMSD_ADD(list_heap_forced_creates, 1);', 'list_heap_creates')
old = 'v->data.list.capacity = capacity < 8 ? 8 : capacity;'
assert r.count(old) == 2
counts['list_initial_capacity_paths'] = 2
r = r.replace(old, old + '\n    EMSD_ADD(list_initial_slots, v->data.list.capacity);')
r = one(r, 'void list_append(Value *list, Value *item) {', 'void list_append(Value *list, Value *item) {\n    EMSD_ADD(list_append_requests, 1);', 'append_requests')
r = one(r, 'Value **new_items = arena_alloc(safe_size_mul(new_cap, sizeof(Value*)));', 'EMSD_ADD(list_arena_growth_allocations, 1);\n            Value **new_items = arena_alloc(safe_size_mul(new_cap, sizeof(Value*)));', 'append_arena_growth')
r = one(r, 'list->data.list.items = xrealloc_array(list->data.list.items, new_cap, sizeof(Value*));', 'EMSD_ADD(list_heap_reallocations, 1);\n            list->data.list.items = xrealloc_array(list->data.list.items, new_cap, sizeof(Value*));', 'append_heap_growth')
r = one(r, 'list->data.list.capacity = new_cap;', 'EMSD_ADD(list_added_slots, new_cap - list->data.list.capacity);\n        list->data.list.capacity = new_cap;', 'append_growth_slots')
for value in ['promoted', 'item']:
    old = 'list->data.list.items[list->data.list.count++] = ' + value + ';'
    r = one(r, old, 'EMSD_ADD(list_append_successes, 1);\n            ' + old, 'append_success_' + value)
for fn in ['env_set_local', 'env_set_local_owned']:
    old = 'void ' + fn + '(Env *env, const char *name, Value *val) {'
    r = one(r, old, old + '\n    EMSD_REGISTER(name, val);', 'name_registration_' + fn)
r = one(r, 'static void gc_collect_impl(Value **seeds, int seed_count) {', 'static void gc_collect_impl(Value **seeds, int seed_count) {\n    EMSD_ADD(gc_requests, 1);', 'gc_requests')
r = one(r, '    g_in_gc = 1;\n\n    GcU u = {0};', '    g_in_gc = 1;\n    EMSD_ADD(gc_collections, 1);\n\n    GcU u = {0};', 'gc_started')
r = one(r, '    /* 2. Internal reference counts (edges from inside U). */', '    EMSD_ADD(gc_scanned_universe_nodes, u.count);\n    /* 2. Internal reference counts (edges from inside U). */', 'gc_scanned')
r = one(r, '    if (bad) {\n        if (eigs_env_flag("EIGS_GC_DEBUG"))', '    if (bad) {\n        EMSD_ADD(gc_accounting_aborts, 1);\n        if (eigs_env_flag("EIGS_GC_DEBUG"))', 'gc_aborted')
r = one(r, '    if (eigs_env_flag("EIGS_GC_DEBUG"))\n        fprintf(stderr, "[gc] universe', '    EMSD_ADD(gc_completed_collections, 1);\n    EMSD_ADD(gc_reclaimed_nodes, garbage);\n    if (eigs_env_flag("EIGS_GC_DEBUG"))\n        fprintf(stderr, "[gc] universe', 'gc_completed')
old = 'static Value *aot_call_dispatch(Value *fn, Value *arg) {'
h = one(h, old, '#ifdef EMS_DIAGNOSTICS\nextern void emsd_dispatch(Value *fn);\n#endif\n' + old + '\n#ifdef EMS_DIAGNOSTICS\n    emsd_dispatch(fn);\n#endif', 'aot_dispatch')
a.out.mkdir(parents=True, exist_ok=True)
(a.out / 'eigenscript.c').write_text(r)
(a.out / 'aot_rt.h').write_text(h)
(a.out / 'ems_diagnostics_metrics.h').write_text('enum { ' + ', '.join('EMSD_' + m for m in metrics) + ', EMSD_COUNT };\nextern void emsd_snapshot(unsigned long long *out);\n')
manifest = {'define': '-DEMS_DIAGNOSTICS', 'instrumentation_only': True, 'inputs': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in [a.runtime, a.header]}, 'sites': counts, 'coverage': ['make_num requests partition into heap allocation, arena allocation and freelist reuse; make_num_permanent requests are a separate always-heap category', 'list constructors are make_list and make_list_heap; append wrappers not double counted; direct element stores outside list_append excluded from append metric', 'list growth counts cover list_append only, not arbitrary builtin insert/resizes; initial capacities are actual normalized slot counts', 'GC scans means unique universe nodes per collection after closure, not total repeated edge/mark passes; reclaimed nodes means collector garbage-node count, not transitive refcount frees or bytes', 'histogram keys are actual BuiltinFn identity, first registered name is merely a label; direct AOT builtin paths bypassing aot_call_dispatch excluded', 'single-threaded diagnostic build; normal exit atexit includes teardown; abort/signal yields no guaranteed report; stdout untouched'], 'static_invariants': ['num_requests == num_heap_allocations + num_arena_allocations + num_freelist_reuses', 'gc_collections == gc_completed_collections + gc_accounting_aborts on successful exit', 'sum(builtin histogram calls) == dispatch_builtin_calls']}
(a.out / 'diagnostics-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps(manifest, indent=2))
