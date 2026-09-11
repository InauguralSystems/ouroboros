#!/usr/bin/env python3
"""Timing-only EMS generated-C ceilings; not semantic certification."""
import argparse
import hashlib
import json
import pathlib
import re

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('baseline', type=pathlib.Path)
p.add_argument('outputdir', type=pathlib.Path)
a = p.parse_args()
src = a.baseline.read_text()
marker = 'static double eig_run_watched_queue_cdcl(double eig_nvars, Value* eig_store, Value* eig_asg, Value* eig_state, Value* eig_stats) {'
assert src.count(marker) == 1
start = src.index(marker)
end = src.index('\n}\n', start) + 3
body = src[start:end]
counts = {}

def once(s, old, new, label):
    n = s.count(old)
    assert n == 1, (label, n)
    counts[label] = n
    return s.replace(old, new)

ra = body
for var, bound in [('old_pos', 'aot_lv_get(&__lv_eig_old)'), ('k', 'aot_lv_get(&__lv_eig_sz)')]:
    pattern = re.compile(r'\{ Value\* _it_' + var + r' = .*?;\n\s+long _n_' + var + r' = aot_iter_len\(_it_' + var + r'\);')
    matches = list(pattern.finditer(ra))
    assert len(matches) == 1, (var, len(matches))
    original = matches[0].group()
    # Keep len's original dynamic call in the outer loop; remove only range.
    if var == 'old_pos':
        m = re.search(r'Value\* _sqca = (\(\{ static AotNameIC.*); aot_call_dispatch\(_sqcf, _sqca\); \}\);', original)
        assert m, original
        bound = m.group(1)
    replacement = ('{ double _probe_bound_' + var + ' = aot_num_ck_at(' + bound + ', "timing-only range bound");\n'
                   '          if (!isfinite(_probe_bound_' + var + ') || _probe_bound_' + var + ' < 0 || _probe_bound_' + var + ' > 2147483647.0 || floor(_probe_bound_' + var + ') != _probe_bound_' + var + ') abort();\n'
                   '          long _n_' + var + ' = (long)_probe_bound_' + var + ';')
    ra = once(ra, original, replacement, 'A_range_' + var)
    ra = once(ra, 'aot_num_ck_at(aot_iter_get(_it_' + var + ', _k_' + var + '), "run_watched_queue_cdcl: for-in ' + var + '")', '(double)_k_' + var, 'A_iter_' + var)
    ra = once(ra, 'val_decref(_it_' + var + ');', '/* timing-only: range allocation absent */', 'A_release_' + var)

helpers = r'''
/* Timing-only slot-expression ceiling. No production semantic certification. */
static int probe_slot_eq_num(EigsSlot s, double n) {
    if (slot_is_num(s)) return s.d == n;
    return aot_eq_n_t(slot_to_value(s), n);
}
static int probe_index_eq(EigsSlot target, EigsSlot index, EigsSlot rhs) {
    EigsSlot result = slot_null();
    aot_lv_index_s(&result, target, index);
    int equal;
    if (slot_is_num(result) && slot_is_num(rhs)) equal = result.d == rhs.d;
    else equal = aot_truthy(aot_eq(slot_to_value(result), slot_to_value(rhs)));
    slot_decref(result);
    return equal;
}
static int probe_sum_index_eq(EigsSlot target, EigsSlot x, EigsSlot y, double rhs) {
    if (slot_is_num(x) && slot_is_num(y))
        return probe_index_eq(target, slot_from_num(num_guard(x.d + y.d)), slot_from_num(rhs));
    return aot_eq_n_t(aot_index_get(slot_to_value(target), aot_add(slot_to_value(x), slot_to_value(y))), rhs);
}
static void probe_sum_index_set(EigsSlot *dst, EigsSlot target, EigsSlot x, EigsSlot y) {
    if (slot_is_num(x) && slot_is_num(y)) {
        aot_lv_index_i(dst, target, num_guard(x.d + y.d));
        return;
    }
    aot_lv_index_v(dst, target, aot_add(slot_to_value(x), slot_to_value(y)));
}
'''
rb = body
for pos in ['wa', 'wb']:
    old = 'aot_eq_n_t(aot_index_get(aot_lv_get(&__lv_eig_store_lits), aot_add(aot_lv_get(&__lv_eig_off), aot_lv_get(&__lv_eig_' + pos + '))), eig_false_lit)'
    rb = once(rb, old, 'probe_sum_index_eq(__lv_eig_store_lits, __lv_eig_off, __lv_eig_' + pos + ', eig_false_lit)', 'B_watch_' + pos)
rb = once(rb, 'aot_ne_n_t(aot_index_get(aot_lv_get(&__lv_eig_clause_deleted), aot_lv_get(&__lv_eig_ci)), 0)', '!probe_index_eq(__lv_eig_clause_deleted, __lv_eig_ci, slot_from_num(0))', 'B_deleted')
rb = once(rb, 'aot_truthy(aot_eq(aot_index_get(aot_lv_get(&__lv_eig_watch_a), aot_lv_get(&__lv_eig_ci)), aot_lv_get(&__lv_eig_false_pos)))', 'probe_index_eq(__lv_eig_watch_a, __lv_eig_ci, __lv_eig_false_pos)', 'B_watch_compare')
rb = once(rb, '{ EigsSlot _sit = __lv_eig_store_lits; Value* _sii = aot_add(aot_lv_get(&__lv_eig_off), aot_lv_get(&__lv_eig_other_pos)); aot_lv_index_v(&__lv_eig_other_lit, _sit, _sii); }', 'probe_sum_index_set(&__lv_eig_other_lit, __lv_eig_store_lits, __lv_eig_off, __lv_eig_other_pos);', 'B_other_lit')
rb = once(rb, 'aot_truthy(({ Value* _x = (aot_ne_n(aot_lv_get(&__lv_eig_false_pos), eig_k)); is_truthy(_x) ? ({ val_decref(_x); (aot_ne_n(aot_lv_get(&__lv_eig_other_pos), eig_k)); }) : _x; }))', '(!probe_slot_eq_num(__lv_eig_false_pos, eig_k) && !probe_slot_eq_num(__lv_eig_other_pos, eig_k))', 'B_position_predicate')

a.outputdir.mkdir(parents=True, exist_ok=True)
outputs = {}
for name, generated in [('range-only', src[:start] + ra + src[end:]), ('slot-expressions-only', src[:start] + helpers + rb + src[end:])]:
    path = a.outputdir / (name + '.c')
    path.write_text(generated)
    outputs[name] = {'path': str(path), 'sha256': hashlib.sha256(generated.encode()).hexdigest()}
report = {'label': 'timing-only; not correctness certification', 'baseline': str(a.baseline), 'baseline_sha256': hashlib.sha256(src.encode()).hexdigest(), 'replacement_counts': counts, 'outputs': outputs, 'A_assumptions': ['range resolves to builtin at both sites', 'bounds are nonnegative finite integers <= INT_MAX (checked)', 'range binding lookup is omitted; len lookup retained'], 'B_scope': 'six expression sites in propagation; signatures and other functions unchanged'}
(a.outputdir / 'probe-manifest.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
