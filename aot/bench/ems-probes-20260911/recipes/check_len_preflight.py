#!/usr/bin/env python3
"""Exercise preparation integrity without compiling or running solver code."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

root=Path('/tmp/ems-close-20260911')
source=root/'len-probe'
runner=root/'run_len_probe.py'
out=Path(tempfile.mkdtemp(prefix='len-preflight-',dir=root))
rows=[]
def check(label,script,expected,diagnostic):
    r=subprocess.run(['python3','-B',str(script),'preflight'],capture_output=True,timeout=10)
    (out/(label+'.stdout')).write_bytes(r.stdout)
    (out/(label+'.stderr')).write_bytes(r.stderr)
    assert r.returncode==expected, (label,r.returncode,r.stderr)
    assert diagnostic in r.stdout+r.stderr
    rows.append({'label':label,'returncode':r.returncode,'expected':expected})
check('pristine',runner,0,b'LEN_STAGE_DONE preflight')
copy=out/'prepared'
copy.mkdir()
for name in [*json.loads((source/'PREP-HASHES.json').read_text()),'PREP-HASHES.json']:
    shutil.copyfile(source/name,copy/name)
alternate=out/'runner.py'
text=runner.read_text()
assert text.count("PROBE=ROOT/'len-probe'")==1
alternate.write_text(text.replace("PROBE=ROOT/'len-probe'",'PROBE=Path('+repr(str(copy))+')'))
recipes=json.loads((copy/'build-recipes.json').read_text())
recipes['candidate-timing'].append('-O0')
(copy/'build-recipes.json').write_text(json.dumps(recipes,indent=2)+'\n')
check('changed-candidate-flags',alternate,1,b'prepared input changed: build-recipes.json')
hashes=json.loads((copy/'PREP-HASHES.json').read_text())
hashes['build-recipes.json']=hashlib.sha256((copy/'build-recipes.json').read_bytes()).hexdigest()
(copy/'PREP-HASHES.json').write_text(json.dumps(hashes,indent=2)+'\n')
check('changed-preparation-fingerprint',alternate,1,b'AssertionError')
(out/'manifest.json').write_text(json.dumps({'cases':rows,'runner_sha256':hashlib.sha256(runner.read_bytes()).hexdigest()},indent=2)+'\n')
print('LEN_PREFLIGHT 3/3: pristine accepted, changed flags and changed fingerprint rejected;',out)
