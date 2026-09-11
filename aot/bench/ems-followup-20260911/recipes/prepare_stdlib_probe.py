#!/usr/bin/env python3
"""Prepare an int_vector-only stdlib-splicing ceiling from pristine main.

This generates source copies only. It neither transpiles nor builds/runs EMS.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--repo', type=Path, default=Path('/home/jon/src/wt/ouro-gap-20260911'))
p.add_argument('--runtime', type=Path, default=Path('/home/jon/src/wt/es-v043'))
p.add_argument('--base', default='328e7692be660b0f56ff73f752207fb25474101c')
p.add_argument('--out', type=Path, default=Path('/tmp/ems-close-20260911/stdlib-probe'))
a = p.parse_args()
a.out = a.out.resolve()
a.runtime = a.runtime.resolve()

def git(*args):
    return subprocess.run(['git', '-C', str(a.repo), *args], check=True, capture_output=True, timeout=30).stdout
def sha(data): return hashlib.sha256(data).hexdigest()
def replace_once(text, old, new):
    assert text.count(old) == 1, (old, text.count(old))
    return text.replace(old, new)

base = git('rev-parse', a.base+'^{commit}').decode().strip()
compiler = git('show', base+':aot/compile.eigs')
frontend = git('show', base+':src/frontend.eigs')
header = git('show', base+':aot/aot_rt.h')
assert b'emit_slot_condition' not in compiler and b'AotScalarRead' not in header, 'not the pristine pre-scalar base'
vector = a.runtime/'lib/int_vector.eigs'
vector_bytes = vector.read_bytes()
runtime_head = subprocess.run(['git', '-C', str(a.runtime), 'rev-parse', 'HEAD'], check=True, capture_output=True, timeout=30).stdout.decode().strip()
runtime_vector = subprocess.run(['git', '-C', str(a.runtime), 'show', runtime_head+':lib/int_vector.eigs'], check=True, capture_output=True, timeout=30).stdout
assert runtime_vector == vector_bytes, 'int_vector differs from runtime HEAD'
text = compiler.decode()
text = replace_once(text, 'load_file of "src/frontend.eigs"', 'load_file of '+json.dumps(str(a.out/'frontend.eigs')))
old = '''                elif lr[1] == "stdlib":
                    if depth > 0:
                        _lf_rewrite_nested of [st, base]
                elif lr[1] == "static":'''
new = '''                elif lr[1] == "stdlib":
                    # TIMING-ONLY: admit exactly the pinned integer-vector
                    # library through the existing static-splicing path.
                    if lr[0] == VECTOR_PATH:
                        full is lr[0]
                        csrc is read_text of full
                    elif depth > 0:
                        _lf_rewrite_nested of [st, base]
                elif lr[1] == "static":'''.replace('VECTOR_PATH', json.dumps(str(vector)))
text = replace_once(text, old, new)
a.out.mkdir(parents=True, exist_ok=False)
(a.out/'baseline-compile.eigs').write_bytes(compiler)
(a.out/'compile-int-vector.eigs').write_text(text)
(a.out/'frontend.eigs').write_bytes(frontend)
(a.out/'aot_rt.h').write_bytes(header)
manifest = {
    'label': 'timing-only int_vector stdlib splicing; not production semantic certification',
    'base_commit': base,
    'runtime_commit': runtime_head,
    'target_library': str(vector),
    'input_hashes': {'aot/compile.eigs': sha(compiler), 'src/frontend.eigs': sha(frontend), 'aot/aot_rt.h': sha(header), str(vector): sha(vector_bytes)},
    'output_hashes': {'compile-int-vector.eigs': sha(text.encode()), 'frontend.eigs': sha(frontend), 'aot_rt.h': sha(header)},
    'source_replacements': {'compiler_frontend_location': 1, 'stdlib_branch_whitelist': 1},
    'mechanism_checks_before_timing': ['native definitions for eig_int_vector_filled and eig_int_vector_fill appear in generated C', 'runtime load_file call for int_vector is absent', 'former int_vector callsites become direct native calls', 'text_builder shim remains the original runtime load', 'no scalar-optimization helper calls appear'],
    'assumptions': ['exact EMS program is the test workload; arbitrary stdlib source is not admitted', 'current static-splicer module binding semantics must be checked through VM output/counters and proof parity', 'stdlib source remains unchanged between preparation and transpilation'],
}
(a.out/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
print(json.dumps(manifest, indent=2))
