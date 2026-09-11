#!/usr/bin/env python3
"""Execute the actual wrapper with in-memory resource/filesystem/driver mocks."""
import builtins
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parent
source=Path('/tmp/ems-close-20260911/run_6x6_no_core.py')
code=compile(source.read_text(),str(source),'exec')
base='/tmp/ems-close-20260911/'
driver=base+'run_larger_ladder.py'
record=base+'scalar-ladder-6x6.core-limit.json'
rows=[]

def check(name,pattern='|apport',original=(0,-1),fault=None):
    files={driver:b'fixed reviewed driver', '/proc/sys/kernel/core_pattern':pattern.encode()}
    sets=[]; entered=[]; fake_sys=SimpleNamespace(argv=['wrapper'])
    if fault=='existing': files[record]=b'prior evidence'
    class MemoryPath:
        def __init__(self,p): self.p=str(p)
        def __truediv__(self,p): return MemoryPath(self.p.rstrip('/')+'/'+p)
        def __str__(self): return self.p
        def exists(self): return self.p in files
        def read_bytes(self): return files[self.p]
        def read_text(self): return files[self.p].decode()
        def write_text(self,s): files[self.p]=s.encode(); return len(s)
    def limits(which):
        assert which==4
        return original
    def setlimits(which,pair):
        assert which==4
        sets.append(tuple(pair))
        if fault=='set-failed' and len(sets)==1: raise OSError('set failed')
        if fault=='restore-failed' and len(sets)==2: raise OSError('restore failed')
    def run(path,run_name):
        entered.append((path,run_name))
        assert path==driver and run_name=='__main__'
        assert fake_sys.argv==[driver,'6x6']
        assert sets[0]==(1 if pattern.startswith('|') else 0,original[1])
        if fault=='driver-failed': raise RuntimeError('driver failed')
        if fault=='interrupted': raise KeyboardInterrupt('cancelled')
        if fault=='driver-drift': files[driver]=b'changed driver'
    modules={'pathlib':SimpleNamespace(Path=MemoryPath),
             'resource':SimpleNamespace(RLIMIT_CORE=4,RLIM_INFINITY=-1,getrlimit=limits,setrlimit=setlimits),
             'runpy':SimpleNamespace(run_path=run),'sys':fake_sys}
    original_import=builtins.__import__
    def importer(n,*a,**kw): return modules[n] if n in modules else original_import(n,*a,**kw)
    b=dict(vars(builtins)); b['__import__']=importer
    error=None
    try: exec(code,{'__builtins__':b,'__name__':'__main__'})
    except BaseException as exc: error=exc
    if fault in ['existing','hard-refusal']:
        assert isinstance(error,AssertionError) and not sets and not entered
        rows.append({'case':name,'result':'rejected before limits/driver'}); return
    r=json.loads(files[record])
    assert sets[-1]==original,'original limit restoration not attempted'
    assert r['driver_unchanged']==(fault!='driver-drift')
    assert r['core_limits_restored']==(fault!='restore-failed')
    expected={None:(None,'driver-returned-success'),
              'driver-failed':(RuntimeError,'ended-with-exception'),
              'interrupted':(KeyboardInterrupt,'ended-with-exception'),
              'set-failed':(OSError,'ended-with-exception'),
              'restore-failed':(OSError,'core-limit-restore-failed'),
              'driver-drift':(RuntimeError,'driver-identity-failed')}[fault]
    assert (error is None) if expected[0] is None else isinstance(error,expected[0])
    assert r['status']==expected[1],r
    assert bool(entered)==(fault!='set-failed')
    rows.append({'case':name,'status':r['status'],'sets':sets,'exception':None if error is None else type(error).__name__})

check('piped-success')
check('ordinary-success',pattern='core')
check('bounded-hard-success',original=(0,7))
for f in ['driver-failed','interrupted','set-failed','restore-failed','driver-drift','existing']:
    check(f,fault=f)
check('hard-refusal',original=(0,0),fault='hard-refusal')
result={'wrapper_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'controls':len(rows),'results':rows,'actual_driver_calls':0,'actual_resource_changes':0}
(ROOT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
