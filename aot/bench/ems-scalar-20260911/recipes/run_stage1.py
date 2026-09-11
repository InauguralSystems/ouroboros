#!/usr/bin/env python3
"""Serialized, bounded diagnostic builds and production-wall comparisons."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ROOT = Path('/tmp/ems-close-20260911')
OURO = Path('/home/jon/src/wt/ouro-gap-20260911')
EMS = Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/EigenMiniSat')
RT = Path('/home/jon/src/wt/es-v043')
sys.path.insert(0, str(EMS / 'benchmarks'))
import compare_native as timing

p = argparse.ArgumentParser()
p.add_argument('stage', choices=['prepare', 'range', 'slots', 'profile', 'counters'])
a = p.parse_args()
env = dict(os.environ, LC_ALL='C', EIGS=str(RT/'src/eigenscript'), EIGS_DIR=str(RT))

def run(label, cmd, cap=600, cwd=OURO):
    print('START', label, flush=True)
    record = timing.run_process([str(x) for x in cmd], ROOT/'processes'/label, cap, env, cwd=cwd)
    if record['returncode'] != 0:
        raise RuntimeError(f'{label}: rc={record["returncode"]}; see {record["directory"]}')
    print('DONE', label, 'wall_s=', record['wall_ns']/1e9, flush=True)
    return Path(record['directory'])

gcc = ['gcc', '-O3', '-ffp-contract=off', '-falign-functions=64', '-falign-jumps=32',
       '-falign-loops=32', '-march=native', '-DEIGENSCRIPT_EXT_HTTP=0',
       '-DEIGENSCRIPT_EXT_MODEL=0', '-DEIGENSCRIPT_EXT_DB=0', '-DEIGENSCRIPT_VERSION="aot"',
       f'-DAOT_SCRIPT_DIR="{EMS}"', f'-DAOT_EXE_DIR="{RT}/src"', '-I'+str(OURO/'aot'), '-I'+str(RT/'src')]

if a.stage == 'prepare':
    source = ROOT/'ems-baseline.c'
    # Saved verbatim from the actual build.sh gcc input while GCC compiled it.
    # Build log/process record identify /tmp/aot_gen.tS9spZ.c as that input.
    assert source.is_file() and source.stat().st_size > 1000000
    run('make-probes', ['python3', ROOT/'make_probes.py', source, ROOT/'probes'], 30)
    for label in ['range-only', 'slot-expressions-only']:
        cmd = gcc + [ROOT/'probes'/f'{label}.c', OURO/'aot/build/libeigsrt.a', '-lm', '-lpthread', '-o', ROOT/f'ems-{label}']
        (ROOT/f'build-{label}.json').write_text(json.dumps([str(x) for x in cmd], indent=2)+'\n')
        run('build-'+label, cmd)
elif a.stage in ('range', 'slots'):
    label = 'range-only' if a.stage == 'range' else 'slot-expressions-only'
    cmd = ['python3', EMS/'benchmarks/compare_native.py', '--aot-binary', ROOT/'ems-baseline',
        '--aot-source-dir', OURO, '--runtime-dir', RT,
        '--build-command', f'env EIGS_DIR={RT} EIGS={RT}/src/eigenscript bash aot/build.sh {EMS}/minisat.eigs {ROOT}/ems-baseline',
        '--build-log', ROOT/'build-baseline.log', '--output', ROOT/('wall-'+label),
        '--candidate-binary', ROOT/f'ems-{label}', '--candidate-label', 'timing-only '+label,
        '--candidate-source', ROOT/'probes'/f'{label}.c', '--candidate-build-command', (ROOT/f'build-{label}.json').read_text(),
        '--candidate-build-log', ROOT/'processes'/('build-'+label)/'stderr']
    d = run('measure-'+label, cmd, 1800, EMS)
    print((d/'stdout').read_text()[-4000:], flush=True)
elif a.stage == 'profile':
    cases = json.loads((ROOT/'wall-range-only/cases.json').read_text())
    case = next(c for c in cases if c['name']=='tseitin-4x4-odd')
    cnf = Path(case['cnf'])
    d = run('profile-baseline', ['perf', 'record', '-e', 'cycles:u', '-F', '199', '--call-graph', 'dwarf',
        '-o', ROOT/'baseline.perf.data', '--', ROOT/'ems-baseline', '--cdcl', cnf], 120, EMS)
    assert timing.sha256(cnf) == case['sha256']
    assert timing.normalized_ems((d/'stdout').read_bytes()) == (cnf.parent/'vm.out.normalized').read_bytes()
    d = run('profile-report', ['perf', 'report', '--stdio', '-i', ROOT/'baseline.perf.data', '--percent-limit', '0.5'], 120)
    (ROOT/'baseline.perf.report').write_bytes((d/'stdout').read_bytes())
elif a.stage == 'counters':
    import statistics
    cases = json.loads((ROOT/'wall-range-only/cases.json').read_text())
    case = next(c for c in cases if c['name']=='tseitin-4x4-odd')
    cnf = Path(case['cnf'])
    reference = (cnf.parent/'vm.out.normalized').read_bytes()
    arms = ['baseline', 'range-only', 'slot-expressions-only']
    records = []
    for repetition in range(5):
        for arm in arms[repetition % 3:] + arms[:repetition % 3]:
            label = f'counter-{repetition}-{arm}'
            counter = ROOT/(label+'.csv')
            rss = ROOT/(label+'.rss')
            binary = ROOT/('ems-'+arm)
            before = timing.sha256(binary)
            d = run(label, ['/usr/bin/time', '-f', '%M', '-o', rss, 'perf', 'stat', '-x', ',',
                '-e', 'instructions:u,cycles:u', '-o', counter, '--', binary, '--cdcl', cnf], 120, EMS)
            assert timing.sha256(binary) == before and timing.sha256(cnf) == case['sha256']
            assert timing.normalized_ems((d/'stdout').read_bytes()) == reference
            events = {}
            for line in counter.read_text().splitlines():
                columns = line.split(',')
                if len(columns)>2 and columns[2] in ('instructions:u', 'cycles:u'):
                    events[columns[2]] = int(columns[0])
            assert len(events) == 2
            records.append(dict(arm=arm, repetition=repetition, binary_sha256=before,
                **events, peak_process_tree_rss_kib=int(rss.read_text())))
    (ROOT/'counter-samples.json').write_text(json.dumps(records, indent=2)+'\n')
    summary = {arm: {key: statistics.median(r[key] for r in records if r['arm']==arm)
        for key in ('instructions:u', 'cycles:u', 'peak_process_tree_rss_kib')} for arm in arms}
    (ROOT/'counter-summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))
print('STAGE_DONE', a.stage, flush=True)
