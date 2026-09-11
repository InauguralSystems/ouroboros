#!/usr/bin/env python3
"""Deferred CLI provenance controls. Hashes/scratch source copies only; no builds.
Run only when root releases the measurement slot. The supplied inventory pin
must come from the externally reviewed preparation record, not recomputation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--prep-sha256',required=True)
    ap.add_argument('--out',type=Path,required=True)
    a=ap.parse_args(); root=Path(__file__).resolve().parent
    raw=(root/'PREP-HASHES.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==a.prep_sha256,'caller pin mismatch'
    inv=json.loads(raw)
    for n,h in inv['files'].items():
        assert hashlib.sha256((root/n).read_bytes()).hexdigest()==h,n
    a.out.mkdir(parents=True,exist_ok=False)
    records=[]
    for label in ['pristine','candidate-flag','candidate-source','inventory-rewrite']:
        dest=a.out/label; dest.mkdir()
        for n in [*inv['files'],'PREP-HASHES.json']:
            (dest/n).write_bytes((root/n).read_bytes())
        if label=='candidate-flag':
            f=dest/'run_prepared.py'; s=f.read_text()
            old="cand=diag+['GC_TRAVERSAL_REUSE']"
            assert s.count(old)==1
            f.write_text(s.replace(old,"cand=diag+[]",1))
        if label in ['candidate-source','inventory-rewrite']:
            f=dest/'eigenscript-probe.c'
            f.write_bytes(f.read_bytes()+b'\n/* intentional provenance control */\n')
        if label=='inventory-rewrite':
            changed=json.loads(raw)
            changed['files']['eigenscript-probe.c']=hashlib.sha256(f.read_bytes()).hexdigest()
            (dest/'PREP-HASHES.json').write_text(json.dumps(changed,indent=2)+'\n')
        cmd=[sys.executable,str(dest/'run_prepared.py'),'preflight','--prep-sha256',a.prep_sha256,'--out',str(dest/'result')]
        r=subprocess.run(cmd,capture_output=True,timeout=60)
        (dest/'stdout').write_bytes(r.stdout); (dest/'stderr').write_bytes(r.stderr)
        expected=0 if label=='pristine' else 1
        record={'label':label,'argv':cmd,'returncode':r.returncode,'expected':expected}
        records.append(record)
        (a.out/'results.json').write_text(json.dumps(records,indent=2)+'\n')
        assert r.returncode==expected,record
        if label=='pristine':
            assert r.stdout==b'GC_PREFLIGHT verified\n'
            assert not json.loads((dest/'result/run.json').read_text())['processes']
        else:
            needle={'candidate-flag':'prepared artifact changed: run_prepared.py',
                    'candidate-source':'prepared artifact changed: eigenscript-probe.c',
                    'inventory-rewrite':'inventory fingerprint mismatch'}[label]
            assert needle.encode() in r.stderr,record
    print('GC_PREFLIGHT_CONTROLS 1 accepted / 3 rejected')
if __name__=='__main__': main()
