#!/usr/bin/env python3
"""Explicitly scheduled serial GC probe builds/controls. Never auto-run by preparation.
EMS workload execution remains the parent's certified benchmark runner's job.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parent
RT=Path('/home/jon/src/wt/es-v043')
EMS=Path('/home/jon/src/InauguralSystems/EigenScriptEcosystem/EigenMiniSat')
ARCHIVE=Path('/home/jon/src/wt/ouro-gap-20260911/aot/build/libeigsrt.a')

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('stage',choices=['preflight','controls','diagnostic-build','timing-build'])
    ap.add_argument('--out',type=Path,required=True,help='new output directory')
    ap.add_argument('--prep-sha256',required=True,help='externally retained PREP-HASHES.json SHA256')
    a=ap.parse_args()
    inventory_path=ROOT/'PREP-HASHES.json'
    raw=inventory_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==a.prep_sha256,'inventory fingerprint mismatch'
    expected=json.loads(raw)  # parse only after verifying caller's external pin
    def verify_inputs():
        assert sha(inventory_path)==a.prep_sha256,'inventory fingerprint mismatch'
        for name,digest in expected['files'].items():
            assert sha(ROOT/name)==digest,f'prepared artifact changed: {name}'
        assert len(expected['runtime_headers'])==20,'runtime header population mismatch'
        for path,digest in expected['runtime_headers'].items():
            assert sha(Path(path))==digest,f'runtime header changed: {path}'
        assert sha(ARCHIVE)==expected['runtime_archive_sha256'],'archive identity changed'
        for path,digest in expected['external_sources'].items():
            assert sha(Path(path))==digest,f'external source changed: {path}'
    verify_inputs()  # before imports, source parsing or build-argument construction
    a.out.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,LC_ALL='C')
    manifest={'stage':a.stage,'processes':[],'outputs':{},
              'prep_sha256':a.prep_sha256,'expected_inventory':expected,'input_checks':[]}
    def save(): (a.out/'run.json').write_text(json.dumps(manifest,indent=2)+'\n')
    def checkpoint(label):
        try:
            verify_inputs()
        except BaseException as exc:
            manifest['input_checks'].append({'label':label,'ok':False,'error':str(exc)})
            manifest['verdict']='input provenance failure'; save(); raise
        manifest['input_checks'].append({'label':label,'ok':True}); save()
    checkpoint('startup')
    if a.stage=='preflight':
        checkpoint('final-preflight')
        manifest['verdict']='preflight inputs verified; no tools imported or executed'; save()
        print('GC_PREFLIGHT verified')
        return
    checkpoint('before-process-helper-import')
    sys.path.insert(0,str(EMS/'benchmarks'))
    import compare_native as process_tools
    checkpoint('after-process-helper-import')
    def run(label,cmd,timeout,expected_rc=0):
        checkpoint('before-'+label)
        try:
            record=process_tools.run_process([str(v) for v in cmd],a.out/'processes'/label,timeout,env,cwd=EMS)
        finally:
            checkpoint('after-'+label)
        manifest['processes'].append(record); save()
        assert not record['timed_out'],f'timeout {label}'
        assert record['returncode']==expected_rc,f'{label}: rc {record["returncode"]} != {expected_rc}'
        return Path(record['directory'])
    base=json.loads((ROOT/'scalar-production-build.json').read_text())
    # Use the original production options, no source/output/archive positional args.
    first_source=base.index('/tmp/ems-close-20260911/ems-scalar-production.c')
    flags=base[:first_source]
    flags[flags.index('-I/home/jon/src/wt/ouro-gap-20260911/aot')]='-I'+str(ROOT)
    run('compiler-version',[base[0],'--version'],15)
    run('archive-members',['ar','t',ARCHIVE],15)
    def build(label,defines,source):
        obj=a.out/(label+'.o'); binary=a.out/label; mapping=a.out/(label+'.map')
        checkpoint('before-build-'+label)
        opts=flags+['-D'+d for d in defines]
        run('compile-'+label,opts+['-c',ROOT/'eigenscript-probe.c','-o',obj],240)
        # Explicit replacement object precedes archive, preventing original TU extraction.
        run('link-'+label,opts+[source,obj,ARCHIVE,'-lm','-lpthread','-Wl,-Map='+str(mapping),'-o',binary],600)
        assert 'libeigsrt.a(eigenscript.o)' not in mapping.read_text(),'original runtime member extracted'
        manifest['outputs'][label]={'binary':str(binary),'sha256':sha(binary),'object_sha256':sha(obj),'map_sha256':sha(mapping),'defines':defines}
        checkpoint('after-build-'+label)
        save(); return binary
    diag=['GC_PROBE_DIAGNOSTICS']; cand=diag+['GC_TRAVERSAL_REUSE']
    if a.stage=='controls':
        logs={}
        for label,defines,rc in [('baseline',diag,0),('candidate',cand,0),
                                 ('missing-duplicate-count',cand+['GC_PROBE_FAULT_DROP_DUP_COUNT'],1),
                                 ('missing-skip-counter',cand+['GC_PROBE_FAULT_ZERO_SKIP_COUNTER'],0)]:
            binary=build(label,defines,ROOT/'control.c')
            log=run('run-'+label,[binary],30,rc); logs[label]=log
            if rc==0:
                assert (log/'stdout').read_text()=='GC_CONTROL failures=0\n'
            else:
                assert 'GC_CONTROL_FAIL duplicate-edge cycle reclaimed' in (log/'stderr').read_text()
        run('validate-positive',['python3',ROOT/'validate.py',logs['baseline']/'stderr',logs['candidate']/'stderr'],15)
        for label,needle in [('missing-duplicate-count','collection-by-collection population/reclamation mismatch'),
                             ('missing-skip-counter','no-child skip path not evidenced')]:
            log=run('validate-'+label,['python3',ROOT/'validate.py',logs['baseline']/'stderr',logs[label]/'stderr'],15,1)
            assert needle in (log/'stderr').read_text(),'unexpected validator failure'
        manifest['verdict']='positive control and two intended rejections observed'
    else:
        definitions=[('baseline',diag),('candidate',cand)] if a.stage=='diagnostic-build' else [('baseline',[]),('candidate',['GC_TRAVERSAL_REUSE'])]
        for label,defs in definitions: build(label,defs,ROOT/'scalar.c')
        manifest['verdict']='build only; no EMS execution or parity claim'
    checkpoint('before-final-verdict')
    save()
if __name__=='__main__': main()
