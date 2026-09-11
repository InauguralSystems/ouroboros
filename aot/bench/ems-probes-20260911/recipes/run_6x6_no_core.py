#!/usr/bin/env python3
"""Run the existing bounded 6x6 ladder without invoking a piped crash dumper."""
import hashlib
import json
from pathlib import Path
import resource
import runpy
import sys

ROOT = Path('/tmp/ems-close-20260911')
driver = ROOT / 'run_larger_ladder.py'
record_path = ROOT / 'scalar-ladder-6x6.core-limit.json'
assert not record_path.exists(), 'refuse to overwrite core-limit evidence'
driver_hash = hashlib.sha256(driver.read_bytes()).hexdigest()
pattern = Path('/proc/sys/kernel/core_pattern').read_text().strip()
original = resource.getrlimit(resource.RLIMIT_CORE)
# Linux ignores a zero RLIMIT_CORE for a piped helper. A limit of exactly one
# byte is its explicit recursion sentinel and skips the helper entirely:
# https://kernel.googlesource.com/pub/scm/linux/kernel/git/stable/linux-stable.git/+/refs/tags/v6.12.27/fs/coredump.c
effective = 1 if pattern.startswith('|') else 0
assert original[1] == resource.RLIM_INFINITY or original[1] >= effective
record = {'driver': str(driver), 'driver_sha256': driver_hash,
          'core_pattern': pattern, 'original_core_limits': list(original),
          'effective_core_limit_bytes': effective,
          'scope': 'same-process wrapper and all solver/checker children',
          'status': 'started'}
record_path.write_text(json.dumps(record, indent=2) + '\n')
failure = None
try:
    resource.setrlimit(resource.RLIMIT_CORE, (effective, original[1]))
    sys.argv = [str(driver), '6x6']
    runpy.run_path(str(driver), run_name='__main__')
except BaseException as exc:
    failure = exc
    record.update(status='ended-with-exception', exception_type=type(exc).__name__, exception=str(exc))
else:
    record['status'] = 'driver-returned-success'
finally:
    try:
        resource.setrlimit(resource.RLIMIT_CORE, original)
        record['core_limits_restored'] = True
    except BaseException as exc:
        record.update(status='core-limit-restore-failed', core_limits_restored=False, restore_error=str(exc))
        failure = failure or exc
    try:
        record['driver_unchanged'] = hashlib.sha256(driver.read_bytes()).hexdigest() == driver_hash
        if not record['driver_unchanged']:
            raise RuntimeError('bounded ladder driver changed during execution')
    except BaseException as exc:
        record.update(status='driver-identity-failed', identity_error=str(exc))
        failure = failure or exc
    record_path.write_text(json.dumps(record, indent=2) + '\n')
if failure is not None:
    raise failure
