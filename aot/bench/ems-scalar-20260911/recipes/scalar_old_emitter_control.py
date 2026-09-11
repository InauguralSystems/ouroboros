#!/usr/bin/env python3
"""Actual pristine-emitter negative control for the scalar emission gate."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
sys.dont_write_bytecode = True
BASE = Path('/tmp/ems-close-20260911')
OUT = BASE/'scalar-old-emitter-control'
REPO = Path('/home/jon/src/wt/ouro-gap-20260911')
RT = Path('/home/jon/src/wt/es-v043')
EMS = Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/EigenMiniSat')
sys.path.insert(0, str(EMS/'benchmarks'))
import compare_native as timing
OUT.mkdir(exist_ok=False)
base = '328e7692be660b0f56ff73f752207fb25474101c'
command = ['git', '-C', str(REPO), 'show', base+':aot/compile.eigs']
result = subprocess.run(command, check=True, capture_output=True, timeout=30)
source = result.stdout.decode()
needle = 'load_file of "src/frontend.eigs"'
assert source.count(needle) == 1
assert 'emit_slot_condition' not in source
compiler = OUT/'compile.eigs'
compiler.write_text(source.replace(needle, 'load_file of '+json.dumps(str(REPO/'src/frontend.eigs'))))
fixture = 't343_slot_scalar_conditions.eigs'
env = dict(os.environ, EIGS_DIR=str(RT), EIGS=str(RT/'src/eigenscript'), LC_ALL='C')
record = timing.run_process([str(RT/'src/eigenscript'), str(compiler), str(REPO/'aot/test'/fixture), str(RT)], OUT/'transpile', 90, env, cwd=REPO)
assert record['returncode'] == 0 and not record['timed_out']
directory = Path(record['directory'])
stdout = (directory/'stdout').read_bytes()
stderr = (directory/'stderr').read_bytes()
assert not stderr
assert b'int main(' in stdout
assert b'aot_scalar_' not in stdout and b'aot_lv_index_add_slots' not in stdout
spec = importlib.util.spec_from_file_location('scalar_emission_gate', REPO/'aot/test/slot_scalar_emission.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
try:
    gate.check(fixture, 0, stdout, stderr)
except ValueError as exc:
    diagnostic = str(exc)
    assert 'expected' in diagnostic and 'got []' in diagnostic
else:
    raise AssertionError('actual old emitter was accepted by scalar emission gate')
manifest = {'base': base, 'old_compiler_sha256': timing.sha256(compiler), 'fixture_sha256': timing.sha256(REPO/'aot/test'/fixture), 'emitted_sha256': timing.sha256(directory/'stdout'), 'actual_gate_rejection': diagnostic}
(OUT/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
print('PASS: actual old emitter rejected by scalar emission gate')
print(json.dumps(manifest, indent=2))
