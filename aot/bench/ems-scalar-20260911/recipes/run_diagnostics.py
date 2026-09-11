#!/usr/bin/env python3
"""Serial bounded diagnostics controls/build; no performance claims."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
sys.dont_write_bytecode = True
ROOT = Path('/tmp/ems-close-20260911')
EMS = Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/EigenMiniSat')
RT = Path('/home/jon/src/wt/es-v043')
OURO = Path('/home/jon/src/wt/ouro-gap-20260911')
BASE_OURO = Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/ouroboros')
sys.path.insert(0, str(EMS / 'benchmarks'))
import compare_native as timing

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--out', type=Path, default=ROOT/'diagnostics-run')
p.add_argument('--build-ems', action='store_true')
p.add_argument('--cnf', type=Path, help='Optional run of instrumented EMS; requires --reference and --build-ems')
p.add_argument('--reference', type=Path, help='Previously validated normalized EMS output')
a = p.parse_args()
if a.cnf and not (a.reference and a.build_ems): p.error('--cnf requires --reference and --build-ems')
a.out.mkdir(parents=True, exist_ok=False)
diag = a.out/'sources'
env = dict(os.environ, LC_ALL='C', EIGS_DIR=str(RT), EIGS=str(RT/'src/eigenscript'))
archive = OURO/'aot/build/libeigsrt.a'

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def run(label, command, timeout=180, expected=0):
    print('START', label, flush=True)
    command = [str(x) for x in command]
    record = timing.run_process(command, a.out/'processes'/label, timeout, env, cwd=EMS)
    if record['timed_out'] or record['returncode'] != expected:
        raise RuntimeError(f'{label}: expected {expected}, got {record}; inspect retained logs')
    print('DONE', label, record['wall_ns']/1e9, flush=True)
    return Path(record['directory'])

run('generate', ['python3', ROOT/'make_diagnostics.py', '--header', BASE_OURO/'aot/aot_rt.h', '--out', diag], 30)
flags = ['gcc', '-O3', '-ffp-contract=off', '-falign-functions=64', '-falign-jumps=32', '-falign-loops=32', '-march=native', '-DEMS_DIAGNOSTICS', '-DEIGENSCRIPT_EXT_HTTP=0', '-DEIGENSCRIPT_EXT_MODEL=0', '-DEIGENSCRIPT_EXT_DB=0', '-DEIGENSCRIPT_VERSION="aot"', f'-DAOT_SCRIPT_DIR="{EMS}"', f'-DAOT_EXE_DIR="{RT}/src"', '-I'+str(diag), '-I'+str(OURO/'aot'), '-I'+str(RT/'src')]
run('archive-members', ['ar', 't', archive], 30)
provenance = {'inputs': {str(path): sha(path) for path in [archive, ROOT/'ems-baseline.c', ROOT/'diagnostics_controls.c', RT/'src/eigenscript.c', BASE_OURO/'aot/aot_rt.h', ROOT/'make_diagnostics.py', ROOT/'run_diagnostics.py']}, 'flags': flags, 'archive_strategy': 'explicit instrumented eigenscript.o precedes baseline archive; linker maps reject old eigenscript.o member', 'outputs': {}}

def build_runtime(label, source):
    obj = a.out/(label+'.o')
    run('compile-'+label, flags + ['-c', source, '-o', obj], 240)
    return obj

def link(label, source, obj):
    binary = a.out/label
    mapping = a.out/(label+'.map')
    run('link-'+label, flags + [source, obj, archive, '-lm', '-lpthread', '-Wl,-Map='+str(mapping), '-o', binary], 600)
    assert 'libeigsrt.a(eigenscript.o)' not in mapping.read_text(), 'old runtime member was linked'
    provenance['outputs'][label] = {'sha256': sha(binary), 'map_sha256': sha(mapping), 'runtime_object_sha256': sha(obj)}
    return binary

def report(directory):
    lines = (directory/'stderr').read_text().splitlines()
    reports = [json.loads(line.removeprefix('EMS_DIAGNOSTICS_JSON ')) for line in lines if line.startswith('EMS_DIAGNOSTICS_JSON ')]
    assert len(reports) == 1, 'expected exactly one diagnostics record'
    return reports[0]

obj = build_runtime('runtime-diagnostic', diag/'eigenscript.c')
control = link('controls', ROOT/'diagnostics_controls.c', obj)
directory = run('controls-positive', [control], 30)
assert (directory/'stdout').read_text() == 'DIAGNOSTICS_CONTROLS failures=0\n'
data = report(directory)
m = data['metrics']
assert m['num_requests'] == m['num_heap_allocations'] + m['num_arena_allocations'] + m['num_freelist_reuses']
assert m['gc_collections'] == m['gc_completed_collections'] + m['gc_accounting_aborts']
assert sum(row['calls'] for row in data['builtin_dispatch_by_identity']) == m['dispatch_builtin_calls'] == 2
assert len(data['builtin_dispatch_by_identity']) == 1, 'aliases split actual identity'
(a.out/'positive-counters.json').write_text(json.dumps(data, indent=2)+'\n')

# Fault is the actual counter increment, not a forged report or changed oracle.
source = (diag/'eigenscript.c').read_text()
needle = 'EMSD_ADD(num_freelist_reuses, 1);'
assert source.count(needle) == 1
fault = diag/'eigenscript-missing-reuse.c'
fault.write_text(source.replace(needle, '/* planted fault: missing reuse increment */'))
faultobj = build_runtime('runtime-fault', fault)
faultcontrol = link('controls-missing-reuse', ROOT/'diagnostics_controls.c', faultobj)
directory = run('controls-fault', [faultcontrol], 30, expected=1)
assert 'CONTROL_FAIL returned number reused' in (directory/'stderr').read_text()
assert 'CONTROL_FAIL whole numeric partition' in (directory/'stderr').read_text()

if a.build_ems:
    binary = link('ems-diagnostics', ROOT/'ems-baseline.c', obj)
    if a.cnf:
        directory = run('ems-counts', [binary, '--cdcl', a.cnf], 240)
        assert timing.normalized_ems((directory/'stdout').read_bytes()) == a.reference.read_bytes()
        data = report(directory)
        m = data['metrics']
        assert m['num_requests'] == m['num_heap_allocations'] + m['num_arena_allocations'] + m['num_freelist_reuses']
        assert m['gc_collections'] == m['gc_completed_collections'] + m['gc_accounting_aborts']
        assert sum(row['calls'] for row in data['builtin_dispatch_by_identity']) == m['dispatch_builtin_calls']
        (a.out/'ems-counters.json').write_text(json.dumps(data, indent=2)+'\n')
        provenance['input_cnf_sha256'] = sha(a.cnf)
        provenance['reference_sha256'] = sha(a.reference)
(a.out/'provenance.json').write_text(json.dumps(provenance, indent=2)+'\n')
print(json.dumps({'positive_controls': 'pass', 'planted_missing_reuse': 'rejected', 'out': str(a.out)}, indent=2))
