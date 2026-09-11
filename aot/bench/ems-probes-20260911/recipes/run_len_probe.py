#!/usr/bin/env python3
"""One selected len-probe stage, scheduled only after the heavy slot is free."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode=True
ROOT=Path('/tmp/ems-close-20260911')
PROBE=ROOT/'len-probe'
EMS=Path('/home/jon/src/wt/ems-cert-20260911')
OURO=Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/ouroboros')
RT=Path('/home/jon/src/wt/es-v043')
sys.path.insert(0,str(EMS/'benchmarks'))
import compare_native as timing
p=argparse.ArgumentParser()
p.add_argument('stage',choices=['preflight','controls','diagnostic','build','compare'])
p.add_argument('--rung',choices=['4x5','5x5'],default='4x5')
a=p.parse_args()
env=dict(os.environ,LC_ALL='C',PYTHONDONTWRITEBYTECODE='1',EIGS_DIR=str(RT),
         EIGS=str(RT/'src/eigenscript'),EIGENSCRIPT_BIN=str(RT/'src/eigenscript'))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
PREP_SHA256='a56415277e58ec8febd416a056a50b78d81cc3ea63467d6b139e98f664bbfca6'
assert sha(PROBE/'PREP-HASHES.json')==PREP_SHA256
prepared=json.loads((PROBE/'PREP-HASHES.json').read_text())
def check_prepared():
    assert sha(PROBE/'PREP-HASHES.json')==PREP_SHA256
    for name,expected in prepared.items():
        assert sha(PROBE/name)==expected, f'prepared input changed: {name}'
check_prepared()
manifest=json.loads((PROBE/'manifest.json').read_text())
recipes=json.loads((PROBE/'build-recipes.json').read_text())
def require_record(name):
    record=json.loads((PROBE/name).read_text())
    assert record['prepared_sha256']==PREP_SHA256, f'stale stage record: {name}'
    return record
def save_record(name,record):
    check_prepared()
    (PROBE/name).write_text(json.dumps(dict(record,prepared_sha256=PREP_SHA256),indent=2)+'\n')
def frozen():
    check_prepared()
    for name,expected in manifest['sha256'].items(): assert sha(PROBE/name)==expected
    assert sha(ROOT/'ems-scalar-production.c')==manifest['source_sha256']
    assert sha(RT/'lib/int_vector.eigs')==json.loads((ROOT/'bulk-probe/manifest.json').read_text())['original_library_sha256']
    assert sha('/home/jon/src/wt/ouro-gap-20260911/aot/build/libeigsrt.a')=='5db0ea14d1ce3476b03643622fea11359172075b21cdc6343d80a9e591bd6eca'
def run(label,command,cap,expected=0):
    print('START',label,flush=True)
    result=timing.run_process([str(x) for x in command],PROBE/'processes'/label,cap,env,cwd=EMS)
    d=Path(result['directory'])
    print('DONE',label,'rc=',result['returncode'],'wall_s=',result['wall_ns']/1e9,flush=True)
    if result['returncode']!=expected or result['timed_out']:
        print((d/'stderr').read_text(errors='replace')[-3000:],flush=True)
        raise RuntimeError(f'{label}: unexpected result; retained {d}')
    return d
def build(name):
    frozen()
    d=run('build-'+name,recipes[name],240)
    record={'argv':recipes[name],'binary_sha256':sha(PROBE/name),
            'inputs':manifest['sha256'],'process_directory':str(d)}
    save_record(name+'.build.json',record)
    return d
frozen()
if a.stage=='preflight':
    pass
elif a.stage=='controls':
    for name in ['control-baseline','control-positive','control-no-reach']:
        build(name)
        d=run('run-'+name,[PROBE/name],30)
        assert (d/'stdout').read_bytes()==b'LEN_CONTROL 4/4\n'
        if name=='control-baseline':
            assert not (d/'stderr').read_bytes()
        else:
            expected=1 if name=='control-no-reach' else 0
            v=run('validate-'+name,['python3','-B',PROBE/'validate_counts.py',d/'stderr','--control'],10,expected)
            if expected:
                lines=[s for s in (d/'stderr').read_text().splitlines() if s.startswith('LEN_PROBE ')]
                assert len(lines)==1 and json.loads(lines[0][10:])=={'entry':4,'eligible':0,'fallback':4}
                assert 'zero reach is not a negative timing result' in (v/'stderr').read_text()
    save_record('controls-complete.json',{'controls':3,'expected_reds':1})
elif a.stage=='diagnostic':
    require_record('controls-complete.json')
    build('ems-diagnostic')
    case=ROOT/'wall-range-only/oracle/case.HTu63l'
    assert sha(case/'input.cnf')=='f0148e8b4ce9692de61347005af8179e8d8d38134283b6fb0c35a8b9efd32b71'
    d=run('ems-diagnostic',[PROBE/'ems-diagnostic','--cdcl',case/'input.cnf'],120)
    assert timing.normalized_ems((d/'stdout').read_bytes())==(case/'vm.out.normalized').read_bytes()
    v=run('validate-ems-diagnostic',['python3','-B',PROBE/'validate_counts.py',d/'stderr'],10)
    save_record('diagnostic-complete.json',{'stdout':'VM byte-exact after timing normalization',
        'counts':json.loads((v/'stdout').read_text()),'process_directory':str(d)})
elif a.stage=='build':
    require_record('diagnostic-complete.json')
    for name in ['baseline-timing','candidate-timing']:
        assert not any('LEN_PROBE_DIAGNOSTICS' in x or 'LEN_PROBE_CONTROL_' in x for x in recipes[name])
        build(name)
else:
    require_record('diagnostic-complete.json')
    assert sha(OURO/'aot/aot_rt.h')==manifest['header_sha256']
    for name in ['baseline-timing','candidate-timing']:
        b=require_record(name+'.build.json')
        assert sha(PROBE/name)==b['binary_sha256']
    args=['--aot-binary',PROBE/'baseline-timing','--aot-source-dir',OURO,
          '--runtime-dir',RT,'--aot-generated-source',PROBE/'baseline.c',
          '--build-command',(PROBE/'baseline-timing.build.json').read_text(),
          '--build-log',PROBE/'processes/build-baseline-timing/stderr',
          '--candidate-binary',PROBE/'candidate-timing','--candidate-source',PROBE/'candidate.c',
          '--candidate-label','timing-only guarded len dispatch scan bypass; boxed result unchanged',
          '--candidate-build-command',(PROBE/'candidate-timing.build.json').read_text(),
          '--candidate-build-log',PROBE/'processes/build-candidate-timing/stderr',
          '--output',ROOT/('wall-len-only-'+a.rung),'--validation','certificate',
          '--drat-trim','/tmp/ems-native-20260910/drat-trim','--extra-rung',a.rung,
          '--timeout','7200','--budget-seconds','21600']
    (PROBE/('compare-'+a.rung+'.command.json')).write_text(json.dumps([str(x) for x in args],indent=2)+'\n')
    os.environ.update(env)
    os.chdir(EMS)
    rc=timing.main([str(x) for x in args])
    frozen()
    if rc: sys.exit(rc)
frozen()
print('LEN_STAGE_DONE',a.stage,flush=True)
