#!/usr/bin/env python3
"""Serialized fresh DMG builds and independent counter/wall consumer checks."""
import argparse
import json
import os
from pathlib import Path
import re
import statistics
import sys

sys.dont_write_bytecode = True
ROOT = Path('/tmp/ems-close-20260911')
OURO = Path('/home/jon/src/wt/ouro-gap-20260911')
BASE = Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/ouroboros')
EMS = BASE.parent/'EigenMiniSat'
DMG = BASE.parent/'DMG'
RT = Path('/home/jon/src/wt/es-v043')
sys.path.insert(0, str(EMS/'benchmarks'))
import compare_native as timing

parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['build', 'counters', 'wall'])
stage = parser.parse_args().stage
environment = dict(os.environ, LC_ALL='C', EIGS_DIR=str(RT), EIGS=str(RT/'src/eigenscript'))

def run(label, command, cwd, cap=600):
    print('START', label, flush=True)
    result = timing.run_process([str(x) for x in command], ROOT/'consumer-processes'/label,
                                cap, environment, cwd=cwd)
    if result['returncode'] != 0:
        raise RuntimeError(f'{label}: rc={result["returncode"]}, see {result["directory"]}')
    print('DONE', label, 'wall_s=', result['wall_ns']/1e9, flush=True)
    return result

def normalize_dmg(data):
    return b'\n'.join(line for line in data.split(b'\n')
                       if not re.search(rb'MHz|[0-9]ms|seconds|cycles/s|elapsed', line))

if stage == 'build':
    archive = OURO/'aot/build/libeigsrt.a'
    archive_hash = timing.sha256(archive)
    provenance = {'archive_sha256': archive_hash, 'dmg': timing.source_snapshot(DMG), 'arms': {}}
    for arm, repo in [('baseline', BASE), ('candidate', OURO)]:
        generated = ROOT/f'dmg-{arm}.c'
        binary = ROOT/f'dmg-{arm}'
        record = run(f'dmg-{arm}-transpile', [RT/'src/eigenscript', repo/'aot/compile.eigs', DMG/'dmg.eigs', RT], repo)
        generated.write_bytes((Path(record['directory'])/'stdout').read_bytes())
        command = ['gcc', '-O3', '-ffp-contract=off', '-falign-functions=64', '-falign-jumps=32',
            '-falign-loops=32', '-march=native', '-DEIGENSCRIPT_EXT_HTTP=0', '-DEIGENSCRIPT_EXT_MODEL=0',
            '-DEIGENSCRIPT_EXT_DB=0', '-DEIGENSCRIPT_VERSION="aot"', f'-DAOT_SCRIPT_DIR="{DMG}"',
            f'-DAOT_EXE_DIR="{RT}/src"', '-I'+str(repo/'aot'), '-I'+str(RT/'src'), generated,
            archive, '-lm', '-lpthread', '-o', binary]
        run(f'dmg-{arm}-build', command, repo)
        provenance['arms'][arm] = {'generated_sha256': timing.sha256(generated),
            'binary_sha256': timing.sha256(binary), 'command': [str(x) for x in command],
            'compiler_sha256': timing.sha256(repo/'aot/compile.eigs'), 'header_sha256': timing.sha256(repo/'aot/aot_rt.h')}
    assert timing.sha256(archive) == archive_hash
    (ROOT/'consumer-builds.json').write_text(json.dumps(provenance, indent=2)+'\n')
else:
    case = next(c for c in json.loads((ROOT/'wall-range-only/cases.json').read_text())
                if c['name'] == 'tseitin-4x4-odd')
    cnf = Path(case['cnf'])
    reference = (cnf.parent/'vm.out.normalized').read_bytes()
    dmg_reference = normalize_dmg((OURO/'aot/test/canary/dmg_cpu_instrs.out').read_bytes())
    workloads = {
        'ems-4x4': {'binaries': {'baseline': ROOT/'ems-baseline', 'candidate': ROOT/'ems-scalar-production'},
                    'args': ['--cdcl', cnf], 'cwd': EMS},
        'dmg-50m': {'binaries': {'baseline': ROOT/'dmg-baseline', 'candidate': ROOT/'dmg-candidate'},
                    'args': ['roms/cpu_instrs.gb', '--cycles', '50000000'], 'cwd': DMG},
    }
    records = []
    for repetition in range(1, 6):
        for name, workload in workloads.items():
            for arm in (['baseline', 'candidate'] if repetition % 2 else ['candidate', 'baseline']):
                binary = workload['binaries'][arm]
                identity = timing.sha256(binary)
                label = f'{stage}-{name}-{repetition}-{arm}'
                rss = ROOT/f'{label}.rss'
                counters = ROOT/f'{label}.csv'
                command = ['/usr/bin/time', '-f', '%M', '-o', rss]
                if stage == 'counters':
                    command += ['perf', 'stat', '-x', ',', '-e', 'instructions:u,cycles:u', '-o', counters, '--']
                command += [binary, *workload['args']]
                record = run(label, command, workload['cwd'], 180)
                directory = Path(record['directory'])
                output = (directory/'stdout').read_bytes()
                assert (directory/'stderr').read_bytes() == b''
                assert timing.sha256(binary) == identity
                if name == 'ems-4x4':
                    assert timing.sha256(cnf) == case['sha256'] and timing.normalized_ems(output) == reference
                else:
                    assert normalize_dmg(output) == dmg_reference and dmg_reference.strip()
                    cycles = re.findall(rb'^Cycles: ([0-9]+)$', output, re.M)
                    assert len(cycles) == 1 and int(cycles[0]) == 50_000_008
                    record['emulated_cycles'] = 50_000_008
                if stage == 'counters':
                    events = {}
                    for line in counters.read_text().splitlines():
                        if not line or line.startswith('#'):
                            continue
                        fields = line.split(',')
                        event = fields[2]
                        assert event in ('instructions:u', 'cycles:u') and event not in events
                        assert int(fields[0]) > 0 and float(fields[4]) >= 99.0
                        events[event] = int(fields[0])
                    assert len(events) == 2
                    record.update(events)
                record.update(case=name, arm=arm, repetition=repetition, binary_sha256=identity,
                              peak_process_tree_rss_kib=int(rss.read_text()), validated=True)
                records.append(record)
                timing.write_json(directory/'validation.json', record)
    fields = ['peak_process_tree_rss_kib'] + (['instructions:u', 'cycles:u'] if stage == 'counters' else ['wall_ns'])
    summary = {}
    for name in workloads:
        summary[name] = {}
        for arm in ('baseline', 'candidate'):
            values = [record for record in records if record['case'] == name and record['arm'] == arm]
            assert len(values) == 5
            summary[name][arm] = {key: {'median': statistics.median(v[key] for v in values),
                'min': min(v[key] for v in values), 'max': max(v[key] for v in values)} for key in fields}
            if name == 'dmg-50m' and stage == 'counters':
                summary[name][arm]['host_instructions_per_emulated_cycle'] = summary[name][arm]['instructions:u']['median']/50_000_008
    timing.write_json(ROOT/f'consumer-{stage}-samples.json', records)
    timing.write_json(ROOT/f'consumer-{stage}-summary.json', summary)
    print(json.dumps(summary, indent=2), flush=True)
print('CONSUMERS_DONE', stage, flush=True)
