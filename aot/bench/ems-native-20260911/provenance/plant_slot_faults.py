from pathlib import Path
import json
import os
import subprocess

repo = Path('/home/jon/src/wt/ouro-ems-20260910/aot')
runtime = Path('/home/jon/src/wt/es-v043')
root = Path('/tmp/ems-gap-20260911/faults')
root.mkdir(exist_ok=False)
header = (repo / 'aot_rt.h').read_text()
start = header.index('static inline int aot_lv_index_fast(')
end = header.index('static inline void aot_lv_index_i(', start)
part = header[start:end]
faults = [
    ('missing_element_incref', 'val_incref(r);', '/* planted: no element ownership */', 0),
    ('missing_destination_release', 'slot_decref(*dst);', '/* planted: leak old destination */', 0),
    ('extra_heap_number_guard', 'slot_from_num(r->data.num)', 'slot_from_num(num_guard(r->data.num))', 0),
    ('missing_buffer_guard', 'aot_lv_set_num(dst, n);', 'slot_decref(*dst); *dst = slot_from_num(n);', 0),
    ('fractional_list_index', 'if ((double)i == d)', 'if (1)', 1),
    ('fractional_buffer_index', 'if ((double)i == d)', 'if (1)', 2),
]
env = dict(os.environ, EIGS_DIR=str(runtime))
records = []
for name, before, after, which in faults:
    lane = root / name / 'aot'
    (lane / 'test').mkdir(parents=True)
    (lane / 'build/asan').mkdir(parents=True)
    # Source copies only. Both controls link the same already-built pinned
    # runtime archive, referenced in place without copying its binary.
    (lane / 'build/asan/libeigsrt.a').symlink_to(repo / 'build/asan/libeigsrt.a')
    changed = part
    if which:
        assert part.count(before) == 2
        pos = -1
        for _ in range(which):
            pos = changed.index(before, pos + 1)
        changed = changed[:pos] + after + changed[pos + len(before):]
    else:
        assert part.count(before) == 1, (name, part.count(before))
        changed = part.replace(before, after, 1)
    (lane / 'aot_rt.h').write_text(header[:start] + changed + header[end:])
    for filename in ('slot_index_ownership.c', 'slot_index_ownership.sh'):
        (lane / 'test' / filename).write_bytes((repo / 'test' / filename).read_bytes())
    print('PLANT', name, flush=True)
    if not which:
        result = subprocess.run(['bash', str(lane / 'test/slot_index_ownership.sh')],
                                env=env, capture_output=True, timeout=180)
        (lane.parent / 'stdout').write_bytes(result.stdout)
        (lane.parent / 'stderr').write_bytes(result.stderr)
        assert result.returncode == 1, (name, result.returncode, result.stdout, result.stderr)
        assert b'baseline witness=1 allocations=253' in result.stdout, (name, result.stdout)
        assert b'FAIL: slot index ownership:' in result.stderr, (name, result.stderr)
        evidence = result.stderr.decode().strip()
    else:
        source = Path('/tmp/ems-gap-20260911/focused/t340_slot_index_errors/generated.c')
        binary = lane.parent / 'candidate'
        command = ['gcc', '-O3', '-ffp-contract=off', '-march=native',
                   '-DEIGENSCRIPT_EXT_HTTP=0', '-DEIGENSCRIPT_EXT_MODEL=0', '-DEIGENSCRIPT_EXT_DB=0',
                   '-I' + str(lane), '-I' + str(runtime / 'src'), str(source),
                   str(repo / 'build/libeigsrt.a'), '-lm', '-lpthread', '-o', str(binary)]
        with (lane.parent / 'build.log').open('wb') as log:
            subprocess.run(command, stdout=log, stderr=log, timeout=180, check=True)
        result = subprocess.run([str(binary)], capture_output=True, timeout=120)
        (lane.parent / 'stdout').write_bytes(result.stdout)
        (lane.parent / 'stderr').write_bytes(result.stderr)
        reference = Path('/tmp/ems-gap-20260911/focused/t340_slot_index_errors/vm.out').read_bytes()
        assert result.returncode == 0 and result.stdout != reference, (name, result.returncode)
        evidence = 't340 byte-exact VM comparison rejects changed fractional-index output'
    records.append({'fault': name, 'rejected': True, 'evidence': evidence})
    print('REJECTED', name, evidence, flush=True)
(root / 'results.json').write_text(json.dumps(records, indent=2) + '\n')
assert len(records) == 6
print('FAULTS_DONE planted=6 rejected=6', flush=True)
