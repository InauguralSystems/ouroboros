from pathlib import Path
import os
import re
import subprocess
import sys

repo = Path('/home/jon/src/wt/ouro-ems-20260910')
runtime = Path('/home/jon/src/wt/es-v043')
root = Path('/tmp/ems-gap-20260911') / (sys.argv[1] if len(sys.argv) > 1 else 'focused')
root.mkdir(exist_ok=False)
count = 0
sources = [repo / 'aot/test' / name for name in sys.argv[2:]] if len(sys.argv) > 2 else sorted((repo / 'aot/test').glob('t34[01]_slot_index*.eigs')) + sorted((repo / 'aot/test').glob('t33[789]_slot_index*.eigs'))
for source in sources:
    dest = root / source.stem
    dest.mkdir()
    print('CHECK', source.name, flush=True)
    with (dest / 'vm.out').open('wb') as out, (dest / 'vm.err').open('wb') as err:
        vm = subprocess.run([str(runtime / 'src/eigenscript'), str(source)], cwd=repo,
                            stdout=out, stderr=err, timeout=120)
    with (dest / 'generated.c').open('wb') as out, (dest / 'transpile.err').open('wb') as err:
        subprocess.run([str(runtime / 'src/eigenscript'), 'aot/compile.eigs', str(source), str(runtime)],
                       cwd=repo, stdout=out, stderr=err, timeout=180, check=True)
    with (dest / 'build.log').open('wb') as log:
        subprocess.run(['/bin/bash', '/tmp/ems-gap-20260911/compile_c.sh', str(dest / 'generated.c'),
                        str(dest / 'candidate'), str(source.parent)], stdout=log, stderr=log, timeout=180, check=True)
    with (dest / 'aot.out').open('wb') as out, (dest / 'aot.err').open('wb') as err:
        native = subprocess.run([str(dest / 'candidate')], cwd=repo, stdout=out, stderr=err, timeout=120)
    assert vm.returncode == native.returncode == 0, (source.name, vm.returncode, native.returncode)
    assert (dest / 'vm.out').read_bytes() == (dest / 'aot.out').read_bytes(), source.name
    assert (dest / 'vm.err').read_bytes() == (dest / 'aot.err').read_bytes(), source.name
    c = (dest / 'generated.c').read_text()
    sites = {suffix: len(re.findall(r'aot_lv_index_' + suffix + r'\(', c)) for suffix in 'siv'}
    if source.name.startswith(('t338', 't339', 't342')):
        assert sum(sites.values()) == 0, (source.name, sites)
    elif source.name.startswith('t337'):
        assert all(sites.values()), sites
    else:
        assert sum(sites.values()) > 0, (source.name, sites)
    count += 1
    print('PASS', source.name, 'emitted', sites, flush=True)
assert count == len(sources) > 0, count
print(f'FOCUSED_DONE matched={count} emission_checks={count}', flush=True)
