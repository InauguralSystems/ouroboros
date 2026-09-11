#!/usr/bin/env python3
"""Run a larger production ladder with bounded address space on the 4GB host."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import runpy
import sys

root=Path('/tmp/ems-close-20260911')
p=argparse.ArgumentParser()
p.add_argument('rung',choices=['5x6','6x6'])
a=p.parse_args()
driver=root/'run_next_probes.py'
data=driver.read_bytes()
metadata_path=root/('scalar-ladder-'+a.rung+'.limits.json')
assert not metadata_path.exists(), 'refuse to overwrite previous resource-limit evidence'
requested=3*1024**3
soft,hard=resource.getrlimit(resource.RLIMIT_AS)
effective=requested if soft==resource.RLIM_INFINITY else min(requested,soft)
if hard!=resource.RLIM_INFINITY: effective=min(effective,hard)
record={'rung':a.rung,'driver':str(driver),'driver_sha256':hashlib.sha256(data).hexdigest(),
        'address_space_soft_limit_bytes':effective,'original_limits':[soft,hard],
        'scope':'wrapper and all child solver/checker/VM processes; inherited RLIMIT_AS',
        'solver_checker_timeout_seconds':7200,'pilot_limit_seconds':600,
        'interpretation':'resource refusal/timeout is incomplete, never a completed timing point',
        'status':'started'}
metadata_path.write_text(json.dumps(record,indent=2)+'\n')
failure=None
try:
    resource.setrlimit(resource.RLIMIT_AS,(effective,hard))
    sys.argv=[str(driver),'ladder-'+a.rung]
    runpy.run_path(str(driver),run_name='__main__')
except BaseException as exc:
    failure=exc
    record['status']='ended-with-exception'
    record['exception_type']=type(exc).__name__
    record['exception']=str(exc)
else:
    record['status']='driver-returned-success'
finally:
    try:
        resource.setrlimit(resource.RLIMIT_AS,(soft,hard))
        record['limits_restored']=True
    except BaseException as exc:
        record['limits_restored']=False
        record['limit_restore_error']=str(exc)
        record['status']='limit-restore-failed'
        failure=failure or exc
    try:
        record['driver_unchanged']=hashlib.sha256(driver.read_bytes()).hexdigest()==record['driver_sha256']
    except BaseException as exc:
        record['driver_unchanged']=False
        record['driver_identity_error']=str(exc)
        record['status']='driver-identity-read-failed'
        failure=failure or exc
    else:
        if not record['driver_unchanged']:
            record['status']='driver-drift-failed'
            failure=failure or RuntimeError('driver changed during execution')
    metadata_path.write_text(json.dumps(record,indent=2)+'\n')
if failure is not None:
    raise failure
