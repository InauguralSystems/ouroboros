#!/usr/bin/env python3
"""One selected, serialized validation or measurement job; retains child records."""
import argparse
import json
import os
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path('/tmp/ems-close-20260911')
EMS = Path('/home/jon/src/wt/ems-cert-20260911')
BASE_EMS = Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/EigenMiniSat')
OURO = Path('/home/jon/src/wt/ouro-gap-20260911')
BASE_OURO = Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/ouroboros')
RT = Path('/home/jon/src/wt/es-v043')
sys.path.insert(0, str(EMS / 'benchmarks'))
import compare_native as timing

parser = argparse.ArgumentParser()
parser.add_argument('stage', choices=['tests', 'oracle-selftest', 'scalar-wall', 'pilot-5x5', 'pilot-5x6', 'pilot-6x6'])
args = parser.parse_args()
environment = dict(os.environ, LC_ALL='C', PYTHONDONTWRITEBYTECODE='1',
                   EIGS=str(RT / 'src/eigenscript'), EIGS_DIR=str(RT),
                   EIGENSCRIPT_BIN=str(RT / 'src/eigenscript'),
                   AOT_BUILD=str(BASE_OURO / 'aot/build.sh'),
                   AOT_BINARY=str(ROOT / 'ems-baseline'), KEEP_WORK='1')

def run(label, command, cap, expected=0):
    print('START', label, flush=True)
    result = timing.run_process([str(x) for x in command], ROOT / 'processes' / label,
                                cap, environment, cwd=EMS)
    directory = Path(result['directory'])
    print('DONE', label, 'rc=', result['returncode'], 'wall_s=', result['wall_ns']/1e9, flush=True)
    print((directory / 'stdout').read_text()[-3500:], flush=True)
    if result['returncode'] != expected:
        print((directory / 'stderr').read_text()[-4500:], flush=True)
        raise RuntimeError(f'{label}: expected rc {expected}, got {result["returncode"]}')
    return directory

if args.stage == 'tests':
    directory = run('certificate-final-tests', ['python3', '-B', '-m', 'unittest', 'discover',
                    '-s', 'benchmarks', '-p', 'test_*.py', '-v'], 180)
    print((directory/'stderr').read_text()[-2000:], flush=True)
elif args.stage == 'oracle-selftest':
    directory = run('certificate-oracle-selftest', ['bash', 'benchmarks/run_native_oracle.sh',
                    '--selftest', '--aot', 'on', '--timeout', '7200'], 7200, expected=1)
    output = (directory/'stdout').read_text()
    lines = [line for line in output.splitlines() if line.startswith('SELFTEST ALL RED')]
    assert len(lines) == 1 and re.search(r'checked=\d+ caught=\d+ controls=\d+ skipped=0 ', lines[0])
    assert ': MISS ' not in output and 'SELFTEST BROKEN' not in output
else:
    rung = '4x5' if args.stage == 'scalar-wall' else args.stage.removeprefix('pilot-')
    label = 'scalar-wall-certificate-4x5' if args.stage == 'scalar-wall' else args.stage
    # Baseline was built in the candidate worktree before any edits, at the
    # identical clean BASE_OURO commit. Do not attribute it to dirty candidate C.
    command = ['python3', '-B', EMS/'benchmarks/compare_native.py',
        '--aot-binary', ROOT/'ems-baseline', '--aot-source-dir', BASE_OURO,
        '--runtime-dir', RT, '--aot-generated-source', ROOT/'ems-baseline.c',
        '--build-command', f'env EIGS_DIR={RT} EIGS={RT}/src/eigenscript bash aot/build.sh {BASE_EMS}/minisat.eigs {ROOT}/ems-baseline',
        '--build-log', ROOT/'build-baseline.log', '--output', ROOT/label,
        '--validation', 'certificate', '--drat-trim', '/tmp/ems-native-20260910/drat-trim',
        '--extra-rung', rung, '--timeout', '7200', '--budget-seconds', '21600']
    if args.stage == 'scalar-wall':
        command += ['--candidate-binary', ROOT/'ems-scalar-production',
            '--candidate-label', 'production scalar slot consumers',
            '--candidate-source', ROOT/'ems-scalar-production.c',
            '--candidate-build-command', (ROOT/'scalar-production-build.json').read_text(),
            '--candidate-build-log', ROOT/'scalar-focused.log']
    else:
        command += ['--pilot-only']
    run(label, command, 21700)
print('STAGE_DONE', args.stage, flush=True)
