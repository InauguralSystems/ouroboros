#!/usr/bin/env python3
"""Run one explicitly selected serial job; never overlaps compiler gates."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path('/tmp/ems-close-20260911')
EMS = Path('/home/jon/src/wt/ems-cert-20260911')
EMS_SOURCE = Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/EigenMiniSat')
OURO_BASE = Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/ouroboros')
OURO = Path('/home/jon/src/wt/ouro-gap-20260911')
RT = Path('/home/jon/src/wt/es-v043')
sys.path.insert(0, str(EMS/'benchmarks'))
import compare_native as timing

p = argparse.ArgumentParser()
p.add_argument('stage', choices=['profile', 'build-stdlib', 'build-bulk', 'compare-stdlib', 'compare-bulk',
                                'ladder-5x5', 'ladder-5x6', 'ladder-6x6'])
a = p.parse_args()
env = dict(os.environ, LC_ALL='C', PYTHONDONTWRITEBYTECODE='1', EIGS_DIR=str(RT),
           EIGS=str(RT/'src/eigenscript'), EIGENSCRIPT_BIN=str(RT/'src/eigenscript'))

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def run(label, command, cap, cwd=EMS):
    print('START', label, flush=True)
    result = timing.run_process([str(x) for x in command], ROOT/'next-processes'/label,
                                cap, env, cwd=cwd)
    directory = Path(result['directory'])
    print('DONE', label, 'rc=', result['returncode'], 'wall_s=', result['wall_ns']/1e9, flush=True)
    if result['returncode'] != 0:
        print((directory/'stdout').read_text(errors='replace')[-1500:], flush=True)
        print((directory/'stderr').read_text(errors='replace')[-3000:], flush=True)
        raise RuntimeError(f'{label} failed; preserved {directory}')
    return directory

def compare(label, command):
    # Keep the timeout/cancellation owner in this process. Nesting run_process
    # around compare_native would put its solver in a second session, beyond
    # the outer owner's kill_session cleanup.
    (ROOT/(label+'.command.json')).write_text(json.dumps([str(x) for x in command], indent=2)+'\n')
    os.environ.update(env)
    os.chdir(EMS)
    print('START', label, flush=True)
    rc = timing.main([str(x) for x in command[3:]])
    print('DONE', label, 'rc=', rc, flush=True)
    return rc

archive = OURO/'aot/build/libeigsrt.a'
assert sha(archive) == '5db0ea14d1ce3476b03643622fea11359172075b21cdc6343d80a9e591bd6eca'
assert sha(ROOT/'ems-baseline.c') == 'ddec94a97948a7f178f924c9b2578b07c0804eed69e7a00b5629c26a0c5c53b2'

if a.stage.startswith('ladder-'):
    rung = a.stage.removeprefix('ladder-')
    assert sha(ROOT/'ems-scalar-production.c') == 'a2d149ba8997c665db5144f1a1c5e1874658e0195df2cbc0aafc257c53106df1'
    assert sha(OURO/'aot/compile.eigs') == 'b334ba546f770f8697f6a5381a049f0ba438b91a7022a9ae0a8ef32df99e5e59'
    assert sha(OURO/'aot/aot_rt.h') == 'ab7c944b18fed52ec1dc0f9d280dab73c296051feb60a6db44bc2dce09d6c4a1'
    command = ['python3', '-B', EMS/'benchmarks/compare_native.py',
        '--aot-binary', ROOT/'ems-scalar-production', '--aot-source-dir', OURO,
        '--runtime-dir', RT, '--aot-generated-source', ROOT/'ems-scalar-production.c',
        '--build-command', (ROOT/'scalar-production-build.json').read_text(),
        '--build-log', ROOT/'scalar-focused.log', '--output', ROOT/('scalar-'+a.stage),
        '--validation', 'certificate', '--drat-trim', '/tmp/ems-native-20260910/drat-trim',
        '--extra-rung', rung, '--timeout', '7200', '--budget-seconds', '21600']
    # The runner records capped failures and refuses n=5 if any pilot >600s.
    rc = compare('scalar-'+a.stage, command)
    if rc:
        sys.exit(rc)
elif a.stage == 'profile':
    case = ROOT/'wall-range-only/oracle/case.HTu63l'
    frozen = json.loads((ROOT/'scalar-focused/manifest.json').read_text())['hashes']
    for path, expected in frozen.items():
        assert sha(path) == expected
    assert sha(case/'input.cnf') == 'f0148e8b4ce9692de61347005af8179e8d8d38134283b6fb0c35a8b9efd32b71'
    profile_inputs = {**frozen, str(case/'input.cnf'): sha(case/'input.cnf'),
                      str(case/'vm.out.normalized'): sha(case/'vm.out.normalized')}
    directory = run('profile-scalar-production', ['perf', 'record', '-e', 'cycles:u', '-F', '199',
        '--call-graph', 'dwarf', '-o', ROOT/'scalar-production.perf.data', '--',
        ROOT/'ems-scalar-production', '--cdcl', case/'input.cnf'], 120, EMS_SOURCE)
    assert timing.normalized_ems((directory/'stdout').read_bytes()) == (case/'vm.out.normalized').read_bytes()
    directory = run('profile-scalar-report', ['perf', 'report', '--stdio', '-i',
        ROOT/'scalar-production.perf.data', '--percent-limit', '0.5'], 120)
    (ROOT/'scalar-production.perf.report').write_bytes((directory/'stdout').read_bytes())
    for path, expected in profile_inputs.items():
        assert sha(path) == expected
    (ROOT/'scalar-production-profile.json').write_text(json.dumps({
        'inputs': profile_inputs, 'data_sha256': sha(ROOT/'scalar-production.perf.data'),
        'report_sha256': sha(ROOT/'scalar-production.perf.report'),
        'scope': 'sampled cycles:u, 199Hz, dwarf stacks; diagnostic only; stdout matches VM'}, indent=2)+'\n')
else:
    kind = a.stage.split('-')[1]
    directory = ROOT/('stdlib-probe' if kind == 'stdlib' else 'bulk-probe')
    name = 'ems-int-vector-only' if kind == 'stdlib' else 'ems-bulk-only'
    source = directory/(name+'.c')
    binary = directory/name
    if a.stage.startswith('build-'):
        if kind == 'stdlib':
            result = run('transpile-int-vector-only', [RT/'src/eigenscript',
                directory/'compile-int-vector.eigs', EMS_SOURCE/'minisat.eigs', RT], 600, OURO)
            assert not (result/'stderr').read_bytes()
            source.write_bytes((result/'stdout').read_bytes())
            code = source.read_text()
            assert 'eig_int_vector_filled(' in code and 'eig_int_vector_fill(' in code
            assert 'aot_call_resolve(__eigs_g, "int_vector_filled"' not in code
            assert 'aot_call_resolve(__eigs_g, "int_vector_fill"' not in code
            assert 'aot_str("lib/int_vector.eigs")' not in code
            assert 'aot_str('+json.dumps(str(RT/'lib/text_builder.eigs'))+')' in code
            assert 'aot_scalar_' not in code
        command = json.loads((ROOT/'scalar-production-build.json').read_text())
        command = [str(source) if x == str(ROOT/'ems-scalar-production.c') else
                   str(binary) if x == str(ROOT/'ems-scalar-production') else x for x in command]
        (directory/'build-command.json').write_text(json.dumps(command, indent=2)+'\n')
        result = run('build-'+kind+'-only', command, 600, OURO)
        manifest = json.loads((directory/'manifest.json').read_text())
        manifest['build'] = {'argv': command, 'process_directory': str(result),
            'generated_sha256': sha(source), 'binary_sha256': sha(binary),
            'header_sha256': sha(directory/'aot_rt.h'), 'archive_sha256': sha(archive)}
        (directory/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    else:
        manifest = json.loads((directory/'manifest.json').read_text())
        assert sha(binary) == manifest['build']['binary_sha256']
        assert sha(source) == manifest['build']['generated_sha256']
        if kind == 'bulk':
            assert sha(manifest['probe_library']) == manifest['probe_library_sha256']
        label = ('timing-only static pinned int_vector' if kind == 'stdlib' else
                 'timing-only existing buf_fill in interpreted int_vector')
        command = ['python3', '-B', EMS/'benchmarks/compare_native.py',
            '--aot-binary', ROOT/'ems-baseline', '--aot-source-dir', OURO_BASE,
            '--runtime-dir', RT, '--aot-generated-source', ROOT/'ems-baseline.c',
            '--build-command', f'env EIGS_DIR={RT} EIGS={RT}/src/eigenscript bash aot/build.sh {EMS_SOURCE}/minisat.eigs {ROOT}/ems-baseline',
            '--build-log', ROOT/'build-baseline.log', '--output', ROOT/('wall-'+kind+'-only'),
            '--validation', 'certificate', '--drat-trim', '/tmp/ems-native-20260910/drat-trim',
            '--candidate-binary', binary, '--candidate-label', label,
            '--candidate-source', source, '--candidate-build-command', json.dumps(manifest),
            '--candidate-build-log', Path(manifest['build']['process_directory'])/'stderr',
            '--timeout', '7200', '--budget-seconds', '21600']
        rc = compare('compare-'+kind+'-only', command)
        if kind == 'bulk':
            assert sha(manifest['probe_library']) == manifest['probe_library_sha256']
        if rc:
            sys.exit(rc)
print('NEXT_STAGE_DONE', a.stage, flush=True)
