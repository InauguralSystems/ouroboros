#!/usr/bin/env python3
"""Run one explicitly scheduled GC experiment stage; no concurrent heavy jobs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode=True
ROOT=Path('/tmp/ems-close-20260911')
PROBE=ROOT/'gc-traversal-probe'
RT=Path('/home/jon/src/wt/es-v043')
EMS=Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/EigenMiniSat')
OURO=EMS.parent/'ouroboros'
PREP='e42272d67a81433bbb65372d57fcce7d9c276c5e227f173477417780d2fe1b87'
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert sha(PROBE/'PREP-HASHES.json')==PREP
prepared=json.loads((PROBE/'PREP-HASHES.json').read_text())
def frozen():
    assert sha(PROBE/'PREP-HASHES.json')==PREP
    for name,digest in prepared['files'].items():
        assert sha(PROBE/name)==digest, name
    for group in ['runtime_headers','external_sources']:
        for path,digest in prepared[group].items():
            assert sha(path)==digest, path
    assert sha(prepared['runtime_archive'])==prepared['runtime_archive_sha256']
frozen()
sys.path.insert(0,str(EMS/'benchmarks'))
import compare_native as timing
p=argparse.ArgumentParser()
p.add_argument('stage',choices=['diagnostic','compare'])
a=p.parse_args()
env=dict(os.environ,LC_ALL='C',PYTHONDONTWRITEBYTECODE='1',EIGS_DIR=str(RT),
         EIGS=str(RT/'src/eigenscript'),EIGENSCRIPT_BIN=str(RT/'src/eigenscript'))
control=json.loads((ROOT/'gc-controls/run.json').read_text())
assert control['prep_sha256']==PREP
assert control['verdict']=='positive control and two intended rejections observed'
assert all(c['ok'] for c in control['input_checks'])

build_records={}
def load_build(directory,diagnostic):
    path=directory/'run.json'
    if directory not in build_records:
        raw=path.read_bytes()
        build_records[directory]=(hashlib.sha256(raw).hexdigest(),json.loads(raw))
    digest,b=build_records[directory]
    assert sha(path)==digest, 'build manifest changed during stage'
    assert b['prep_sha256']==PREP
    assert b['verdict']=='build only; no EMS execution or parity claim'
    assert all(c['ok'] for c in b['input_checks'])
    assert set(b['outputs'])=={'baseline','candidate'}
    for label,data in b['outputs'].items():
        expected=(['GC_PROBE_DIAGNOSTICS'] if diagnostic else [])
        if label=='candidate': expected+=['GC_TRAVERSAL_REUSE']
        assert data['defines']==expected
        assert sha(data['binary'])==data['sha256']
        assert sha(directory/(label+'.o'))==data['object_sha256']
        assert sha(directory/(label+'.map'))==data['map_sha256']
    assert sha(path)==digest, 'build manifest changed during verification'
    return b

if a.stage=='diagnostic':
    directory=ROOT/'gc-diagnostic-build'
    build=load_build(directory,True)
    out=ROOT/'gc-diagnostic-workload'
    out.mkdir(exist_ok=False)
    case=ROOT/'wall-range-only/oracle/case.HTu63l'
    assert sha(case/'input.cnf')=='f0148e8b4ce9692de61347005af8179e8d8d38134283b6fb0c35a8b9efd32b71'
    reference=(case/'vm.out.normalized').read_bytes()
    case_hashes={str(p):sha(p) for p in [case/'input.cnf',case/'vm.out.normalized']}
    records=[]
    for label in ['baseline','candidate']:
        frozen(); load_build(directory,True)
        print('GC_DIAGNOSTIC',label,flush=True)
        r=timing.run_process(['/usr/bin/time','-v','-o',str(out/(label+'.time')),'--',
            build['outputs'][label]['binary'],'--cdcl',str(case/'input.cnf')],
            out/label,240,env,cwd=EMS)
        frozen(); load_build(directory,True)
        assert r['returncode']==0 and not r['timed_out']
        assert timing.normalized_ems((out/label/'stdout').read_bytes())==reference
        records.append(r)
    r=timing.run_process(['python3','-B',str(PROBE/'validate.py'),str(out/'baseline/stderr'),
        str(out/'candidate/stderr')],out/'validate',15,env,cwd=EMS)
    assert r['returncode']==0 and not r['timed_out']
    frozen(); load_build(directory,True)
    for path,digest in case_hashes.items(): assert sha(path)==digest
    result={'prep_sha256':PREP,'status':'complete','processes':records,
            'build_manifest_sha256':build_records[directory][0],'case_hashes':case_hashes,
            'validation':'both stdout match VM after timing normalization; exact GC population sequence',
            'metrics':json.loads((out/'validate/stdout').read_text())}
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print('GC_DIAGNOSTIC_COMPLETE',json.dumps(result['metrics']),flush=True)
else:
    diagnostic=json.loads((ROOT/'gc-diagnostic-workload/result.json').read_text())
    assert diagnostic['prep_sha256']==PREP and diagnostic['status']=='complete'
    directory=ROOT/'gc-timing-build'
    build=load_build(directory,False)
    assert sha(OURO/'aot/aot_rt.h')==sha(PROBE/'aot_rt.h')
    args=['--aot-binary',directory/'baseline','--aot-source-dir',OURO,'--runtime-dir',RT,
          '--aot-generated-source',PROBE/'scalar.c','--build-command',json.dumps(build),
          '--build-log',directory/'processes/link-baseline/stderr',
          '--candidate-binary',directory/'candidate','--candidate-source',PROBE/'scalar.c',
          '--candidate-label','timing-only GC traversal reuse runtime overlay; same scalar C',
          '--candidate-build-command',json.dumps(build),
          '--candidate-build-log',directory/'processes/link-candidate/stderr',
          '--output',ROOT/'wall-gc-traversal-4x5','--validation','certificate',
          '--drat-trim','/tmp/ems-native-20260910/drat-trim','--extra-rung','4x5',
          '--timeout','7200','--budget-seconds','21600']
    (ROOT/'gc-compare-command.json').write_text(json.dumps([str(x) for x in args],indent=2)+'\n')
    os.environ.update(env); os.chdir(EMS)
    rc=timing.main([str(x) for x in args])
    frozen(); load_build(directory,False)
    if rc: sys.exit(rc)
    print('GC_COMPARE_COMPLETE',flush=True)
