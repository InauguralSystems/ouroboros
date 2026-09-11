#!/usr/bin/env python3
"""Prepare a timing-only interpreted int_vector variant using existing buf_fill."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path('/tmp/ems-close-20260911')
REPO = Path('/home/jon/src/wt/ouro-gap-20260911')
RT = Path('/home/jon/src/wt/es-v043')
BASE = '328e7692be660b0f56ff73f752207fb25474101c'
OUT = ROOT/'bulk-probe'
OUT.mkdir(exist_ok=False)
library_path = RT/'lib/int_vector.eigs'
original = library_path.read_text()
loop = '    for i in range of (len of vec):\n        vec[i] is fill_value\n'
assert original.count(loop) == 2
changed = original.replace(loop, '    buf_fill of [vec, 0, len of vec, fill_value]\n')
library = OUT/'int_vector-bulk.eigs'
library.write_text(changed)
source = (ROOT/'ems-baseline.c').read_text()
old = 'aot_str("lib/int_vector.eigs")'
assert source.count(old) == 1
source = source.replace(old, 'aot_str('+json.dumps(str(library))+')')
assert 'aot_scalar_' not in source
assert 'aot_call_resolve(__eigs_g, "int_vector_filled"' in source
assert 'aot_call_resolve(__eigs_g, "int_vector_fill"' in source
generated = OUT/'ems-bulk-only.c'
generated.write_text(source)
header = subprocess.run(['git','-C',str(REPO),'show',BASE+':aot/aot_rt.h'],
                        check=True,capture_output=True,timeout=30).stdout
(OUT/'aot_rt.h').write_bytes(header)
sha = lambda value: hashlib.sha256(value).hexdigest()
manifest = {
    'scope': 'timing-only existing-buf_fill library experiment; no general semantic certification',
    'compiler_base': BASE,
    'runtime_head': subprocess.run(['git','-C',str(RT),'rev-parse','HEAD'],check=True,capture_output=True,timeout=30).stdout.decode().strip(),
    'original_library': str(library_path), 'original_library_sha256': sha(original.encode()),
    'probe_library': str(library), 'probe_library_sha256': sha(changed.encode()),
    'baseline_generated_sha256': sha((ROOT/'ems-baseline.c').read_bytes()),
    'probe_generated_sha256': sha(source.encode()), 'header_sha256': sha(header),
    'source_changes': 'two int_vector fill loops become existing buf_fill calls; one generated-C load literal points to this isolated library',
    'unchanged': 'all EMS search code and policies; both vector functions remain interpreted; no runtime C or scalar compiler changes',
    'limits': ['real EMS vectors are buffers; generic list arguments accepted by the old loop are outside this probe',
               'general library promotion must assess empty buffers, numeric flags, binding/callback behavior, and observer/trace semantics',
               'new builtin dependency is an implementation change, not a proof of compiler equivalence for arbitrary rebinding'],
}
(OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('BULK_PROBE_PREPARED',json.dumps(manifest),flush=True)
