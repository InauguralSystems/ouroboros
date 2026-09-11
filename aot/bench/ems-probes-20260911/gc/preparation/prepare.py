#!/usr/bin/env python3
"""Source-only GC traversal experiment; exact reversible replacement ledger."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RT = Path('/home/jon/src/wt/es-v043/src/eigenscript.c')
def sha(b): return hashlib.sha256(b).hexdigest()
def put(p,b):
    if p.exists():
        assert p.read_bytes()==b, f'refusing changed frozen artifact {p}'
    else: p.write_bytes(b)

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source',type=Path,default=RT)
    ap.add_argument('--out',type=Path,default=ROOT)
    a=ap.parse_args()
    raw=a.source.read_bytes()
    # Original hash is pinned independently in source-pin.json at preparation.
    pin=json.loads((ROOT/'source-pin.json').read_text())
    assert sha(raw)==pin['eigenscript.c'], 'runtime source identity mismatch'
    original=raw.decode(); text=original; ledger=[]
    def sub(label,old,new,count=1):
        nonlocal text
        actual=text.count(old)
        assert actual==count, f'{label}: {actual} != {count}'
        text=text.replace(old,new)
        ledger.append({'label':label,'count':count,'old':old,'new':new})
    sub('diagnostic implementation', '#include <pthread.h>\n', '#include <pthread.h>\n'+(ROOT/'metrics_impl.inc').read_text()+'\n')
    sub('collection-local child bit', '    uint8_t  *mark;\n', '    uint8_t  *mark;\n#ifdef GC_TRAVERSAL_REUSE\n    uint8_t *has_node_children;\n#endif\n')
    sub('grow child bit', '        u->mark     = xrealloc_array(u->mark, u->cap, sizeof(uint8_t));', '        u->mark     = xrealloc_array(u->mark, u->cap, sizeof(uint8_t));\n#ifdef GC_TRAVERSAL_REUSE\n        u->has_node_children = xrealloc_array(u->has_node_children, u->cap, sizeof(uint8_t));\n#endif')
    sub('initialize child bit', '    u->mark[n] = 0;\n', '    u->mark[n] = 0;\n#ifdef GC_TRAVERSAL_REUSE\n    u->has_node_children[n] = 0;\n#endif\n')
    # Insert into the shared WALK, never duplicate/alter the edge table.
    sub('walk inspected slot count', '            if (IS_NODE) {', '            GCP_SLOT(_k); \\\n            if (IS_NODE) {')
    sub('walk accepted edge count', '                void *OUT_OBJ = (void *)(CHILD);', '                GCP_EDGE(_k); \\\n                void *OUT_OBJ = (void *)(CHILD);')
    sub('clear slot count', '            CLEAR                                                             \\', '            { GCP_SLOT(_k); CLEAR }                                          \\')
    sub('collection attempts', 'static void gc_collect_impl(Value **seeds, int seed_count) {\n    if (g_in_gc || g_vm_multithreaded) return;', 'static void gc_collect_impl(Value **seeds, int seed_count) {\n    GCP_ENTER();\n    if (g_in_gc || g_vm_multithreaded) { GCP_ADD(skipped_busy,1); return; }')
    sub('empty collection count', '    if (!g_gc_envs && seed_count == 0) {\n        g_gc_threshold', '    if (!g_gc_envs && seed_count == 0) {\n        GCP_ADD(skipped_empty,1);\n        g_gc_threshold')
    sub('collection start count', '    g_in_gc = 1;\n\n    GcU u = {0};', '    g_in_gc = 1;\n    GCP_ADD(started,1);\n\n    GcU u = {0};')
    old='''    for (int n = 0; n < u.count; n++) {
        GC_FOR_EACH_CHILD(&u, n, child, child_kind, {
            gcu_add(&u, child, child_kind);
        });
    }

    /* 2. Internal reference counts (edges from inside U). */'''
    new='''    GCP_PHASE(0);
    for (int n = 0; n < u.count; n++) {
        GC_FOR_EACH_CHILD(&u, n, child, child_kind, {
            gcu_add(&u, child, child_kind);
#ifdef GC_TRAVERSAL_REUSE
            /* Indices survive gcu_add's array reallocations. Every owned
             * edge increments, including duplicates and self edges. */
            int ci = gcu_find(&u, child);
            if (ci < 0) abort();
            u.has_node_children[n] = 1;
#ifdef GC_PROBE_FAULT_DROP_DUP_COUNT
            if (u.internal[ci] == 0) u.internal[ci]++;
#else
            u.internal[ci]++;
#endif
#endif
        });
    }

#ifndef GC_TRAVERSAL_REUSE
    GCP_PHASE(1);
    /* 2. Internal reference counts (edges from inside U). */'''
    sub('fuse discovery and internal counts',old,new)
    sub('close original internal pass','    /* 3. Roots: refcount > internal + collector pins.', '#endif\n    /* 3. Roots: refcount > internal + collector pins.')
    sub('count accounting abort', '    if (bad) {\n        if (eigs_env_flag', '    if (bad) {\n        GCP_ADD(aborted,1);\n        if (eigs_env_flag')
    sub('free child bit', 'free(u.internal); free(u.pinned); free(u.mark);', 'free(u.internal); free(u.pinned); free(u.mark);\n#ifdef GC_TRAVERSAL_REUSE\n    free(u.has_node_children);\n#endif',2)
    sub('mark phase and no-child skip', '''    /* 4. Mark everything reachable from the roots within U. */
    while (sp > 0) {
        int n = stack[--sp];''', '''    /* 4. Mark everything reachable from the roots within U. */
    GCP_PHASE(2);
    while (sp > 0) {
        int n = stack[--sp];
#ifdef GC_TRAVERSAL_REUSE
        if (!u.has_node_children[n]) { GCP_NOCHILD(); continue; }
#endif''')
    sub('clear phase', '    if (garbage) {\n        for (int n', '    GCP_PHASE(3);\n    if (garbage) {\n        for (int n')
    sub('completed collection populations', '    if (eigs_env_flag("EIGS_GC_DEBUG"))\n        fprintf(stderr, "[gc] universe', '    GCP_COLLECTION(u.count,garbage,seed_count);\n    if (eigs_env_flag("EIGS_GC_DEBUG"))\n        fprintf(stderr, "[gc] universe')
    # Reverse the edit sequence as an independent byte-coverage assertion.
    restored=text
    for e in reversed(ledger):
        assert restored.count(e['new'])==e['count'], e['label']
        restored=restored.replace(e['new'],e['old'])
    assert restored==original
    a.out.mkdir(parents=True,exist_ok=True)
    put(a.out/'eigenscript-original.c',raw)
    put(a.out/'eigenscript-probe.c',text.encode())
    for name in ['gc_probe_metrics.h','metrics_impl.inc']:
        put(a.out/name,(ROOT/name).read_bytes())
    put(a.out/'replacement-ledger.json',(json.dumps(ledger,indent=2)+'\n').encode())
    manifest={'prepared_only':True,'runtime_pin':'a6c50fba6a6250ea347a34500d6c9fa503a5c931',
              'input_path':str(a.source.resolve()),'input_sha256':sha(raw),'output_sha256':sha(text.encode()),
              'generator_sha256':sha(Path(__file__).read_bytes()),'exact_reversible':True,
              'edit_groups':len(ledger),'changed_occurrences':sum(e['count'] for e in ledger),
              'variants':{'baseline':[],'candidate':['GC_TRAVERSAL_REUSE'],
                          'diagnostic_baseline':['GC_PROBE_DIAGNOSTICS'],
                          'diagnostic_candidate':['GC_PROBE_DIAGNOSTICS','GC_TRAVERSAL_REUSE']}}
    put(a.out/'manifest.json',(json.dumps(manifest,indent=2)+'\n').encode())
    print(json.dumps(manifest))
if __name__=='__main__': main()
