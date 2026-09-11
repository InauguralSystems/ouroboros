#!/usr/bin/env python3
"""Lightweight actual-function provenance control; mock bytes, no runtime jobs."""
import ast
import copy
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
SOURCE=Path('/tmp/ems-close-20260911/run_gc_workload.py')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
parsed=ast.parse(SOURCE.read_text())
fn=next(n for n in parsed.body if isinstance(n,ast.FunctionDef) and n.name=='load_build')
prep=next(n.value.value for n in parsed.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PREP' for t in n.targets))
def instantiate(function):
    namespace={'hashlib':hashlib,'json':json,'sha':sha,'PREP':prep,'build_records':{}}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[])),str(SOURCE),'exec'),namespace)
    return namespace
current=instantiate(copy.deepcopy(fn))
# Construct the old moving-manifest behavior from the same function: replace
# cache setup with fresh JSON parsing, removing only the manifest-pin asserts.
old=copy.deepcopy(fn)
assert isinstance(old.body[1],ast.If)
old.body[1:4]=ast.parse('b=json.loads(path.read_bytes())').body
old.body=[n for n in old.body if not (isinstance(n,ast.Assert) and isinstance(n.msg,ast.Constant) and str(n.msg.value).startswith('build manifest changed'))]
previous=instantiate(old)
directory=ROOT/'mock-build'; directory.mkdir(exist_ok=False)
manifest={'prep_sha256':prep,'verdict':'build only; no EMS execution or parity claim',
          'input_checks':[{'ok':True}],'outputs':{}}
for label in ['baseline','candidate']:
    for suffix in ['', '.o','.map']:
        (directory/(label+suffix)).write_bytes(('mock '+label+suffix+' v1').encode())
    manifest['outputs'][label]={'defines':['GC_PROBE_DIAGNOSTICS']+(['GC_TRAVERSAL_REUSE'] if label=='candidate' else []),
        'binary':str(directory/label),'sha256':sha(directory/label),
        'object_sha256':sha(directory/(label+'.o')),'map_sha256':sha(directory/(label+'.map'))}
path=directory/'run.json'; path.write_text(json.dumps(manifest))
initial=sha(path)
current['load_build'](directory,True)
previous['load_build'](directory,True)
(directory/'candidate').write_bytes(b'mock candidate changed v2')
manifest['outputs']['candidate']['sha256']=sha(directory/'candidate')
path.write_text(json.dumps(manifest))
previous['load_build'](directory,True)  # old behavior incorrectly accepts new expectations
try:
    current['load_build'](directory,True)
except AssertionError as exc:
    assert str(exc)=='build manifest changed during stage'
    rejection=str(exc)
else:
    raise AssertionError('current gate accepted moving manifest')
assert current['build_records'][directory][0]==initial
result={'source':str(SOURCE),'source_sha256':sha(SOURCE),'control_sha256':sha(__file__),
        'pristine_current':'accepted','pristine_old':'accepted',
        'changed_binary_and_manifest_old':'accepted (expected fault witness)',
        'changed_binary_and_manifest_current':'rejected','diagnostic':rejection,
        'initial_manifest_sha256':initial,'changed_manifest_sha256':sha(path),
        'runtime_jobs':0}
(ROOT/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
