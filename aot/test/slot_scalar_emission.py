#!/usr/bin/env python3
"""Non-vacuous emission assertions for scalar slot consumers."""
import os
from pathlib import Path
import re
import subprocess
import sys

FIXTURES = {
    't343_slot_scalar_conditions.eigs': {'aot_scalar_slot', 'aot_scalar_index', 'aot_scalar_equal', 'aot_scalar_add_slots', 'aot_lv_index_add_slots'},
    't344_slot_scalar_errors.eigs': {'aot_scalar_index', 'aot_scalar_equal', 'aot_lv_index_add_slots'},
    't345_slot_scalar_callbacks.eigs': set(),
    't346_slot_scalar_traced.eigs': set(),
    't347_slot_scalar_observed.eigs': set(),
    't348_slot_scalar_rhs_excluded.eigs': set(),
}
PATTERN = r'\b(aot_scalar_\w+|aot_lv_index_add_slots)\s*\('

def check(name, rc, stdout, stderr):
    if rc or stderr:
        raise ValueError(f'{name}: transpile rc={rc}, stderr={len(stderr)}')
    code = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', ' ', stdout.decode(), flags=re.S)
    if not re.search(r'\bint\s+main\s*\(', code):
        raise ValueError(f'{name}: missing executable emission')
    calls = re.findall(PATTERN, code)
    required = FIXTURES[name]
    if (required and not required.issubset(calls)) or (not required and calls):
        raise ValueError(f'{name}: expected {sorted(required)}, got {sorted(set(calls))}')
    return {name: calls.count(name) for name in sorted(set(calls))}

def selftest():
    cases = 0
    def reject(*args):
        nonlocal cases
        try:
            check(*args)
        except ValueError:
            cases += 1
        else:
            raise AssertionError('accepted planted emission fault')
    for name, required in FIXTURES.items():
        good = ('int main(void) {' + ''.join(fn+'();' for fn in sorted(required)) + '}').encode()
        check(name, 0, good, b'')
        cases += 1
        if required:
            for fn in sorted(required):
                reject(name, 0, good.replace((fn+'();').encode(), b''), b'')
            reject(name, 0, b'int main(void) {} /* '+good+b' */', b'')
        else:
            reject(name, 0, b'int main(void) { aot_scalar_equal(); }', b'')
        reject(name, 9, good, b'')
        reject(name, 0, good, b'unexpected diagnostics')
        reject(name, 0, b'', b'')
    print(f'PASS: slot scalar emission selftests cases={cases}')

def main():
    if sys.argv[1:] == ['--selftest']:
        selftest()
        return
    if sys.argv[1:]:
        raise ValueError('usage: slot_scalar_emission.py [--selftest]')
    aot = Path(__file__).resolve().parents[1]
    rt = Path(os.environ.get('EIGS_DIR', '../../EigenScript'))
    rt = (aot / rt).resolve()
    eig = os.environ.get('EIGS', str(rt/'src/eigenscript'))
    if '/' in eig:
        eig = str((aot/eig).resolve())
    completed = 0
    for name in FIXTURES:
        fixture = aot/'test'/name
        if not fixture.is_file() or not fixture.read_bytes().strip():
            raise ValueError(f'missing/empty fixture: {fixture}')
        result = subprocess.run([eig, str(aot/'compile.eigs'), str(fixture), str(rt)], cwd=aot.parent, capture_output=True, timeout=60)
        counts = check(name, result.returncode, result.stdout, result.stderr)
        completed += 1
        print(f'PASS: slot scalar emission {name} {counts}', flush=True)
    print(f'SLOT_SCALAR_EMISSION_DONE fixtures={completed} checks={completed}')

if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f'FAIL: slot scalar emission: {exc}', file=sys.stderr)
        sys.exit(1)
