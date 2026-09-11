#!/usr/bin/env python3
"""Plant real scalar helper faults against the strict ownership A/B gate.

Run after aot/build/asan/libeigsrt.a exists; EIGS_DIR must name the pinned
runtime. Every fault must leave baseline healthy and reject the candidate.
"""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile

FAULTS = {
    'sum_materialization_guard': ('return aot_scalar_number(num_guard(l.number + r.number));', 'return aot_scalar_number(l.number + r.number);'),
    'heap_nan_identity': ('value->type == VAL_NUM && !isnan(value->data.num)', 'value->type == VAL_NUM'),
    'list_read_clamp': ('double result = element->data.num; /* raw even for arena nums */', 'double result = num_guard(element->data.num); /* planted extra clamp */'),
    'buffer_read_guard': ('double result = num_guard(container->data.buffer.data[(int)number]);', 'double result = container->data.buffer.data[(int)number];'),
    'slot_materialization_guard': ('return aot_scalar_number(num_guard(slot.d));', 'return aot_scalar_number(slot.d);'),
    'left_value_release': ('val_decref(left.value);', '/* planted missing left release */'),
    'right_value_release': ('val_decref(right.value);', '/* planted missing right release */'),
}

def main():
    if sys.argv[1:]:
        raise ValueError('configure EIGS_DIR/AOT_ARCH; no positional arguments')
    aot = Path(__file__).resolve().parents[1]
    header = (aot/'aot_rt.h').read_text()
    archive = aot/'build/asan/libeigsrt.a'
    if not archive.is_file() or not archive.stat().st_size:
        raise ValueError('build the pinned ASan archive before this check')
    root = Path(tempfile.mkdtemp(prefix='aot-slot-scalar-faults-'))
    print(f'scalar fault artifacts: {root}', flush=True)
    for name, (old, new) in FAULTS.items():
        if header.count(old) != 1:
            raise ValueError(f'{name}: expected exactly one actual helper site')
        lane = root/name
        (lane/'test').mkdir(parents=True)
        (lane/'aot_rt.h').write_text(header.replace(old, new))
        (lane/'build').symlink_to(aot/'build', target_is_directory=True)
        (lane/'test/slot_index_ownership.c').symlink_to(aot/'test/slot_index_ownership.c')
        shutil.copyfile(aot/'test/slot_index_ownership.sh', lane/'test/slot_index_ownership.sh')
        command = ['bash', str(lane/'test/slot_index_ownership.sh')]
        (lane/'command.json').write_text(json.dumps(command)+'\n')
        with (lane/'stdout').open('wb') as stdout, (lane/'stderr').open('wb') as stderr:
            child = subprocess.Popen(command, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                rc = child.wait(timeout=180)
            except BaseException:
                # The ownership driver's signal handler kills/reaps its own
                # compiler session; give that bounded cleanup time to finish.
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait(timeout=5)
                raise
        (lane/'rc').write_text(str(rc)+'\n')
        out = (lane/'stdout').read_text()
        err = (lane/'stderr').read_text()
        if rc != 1 or 'slot index ownership: baseline witness=1' not in out:
            raise ValueError(f'{name}: fault did not reach a healthy baseline and candidate rejection')
        if 'FAIL: slot index ownership:' not in err or 'PASS: slot index ownership arms=' in out:
            raise ValueError(f'{name}: planted fault accepted or malformed verdict')
        if 'ASan compile failed' in err:
            raise ValueError(f'{name}: compilation failure is not a rejected runtime fault')
        print(f'PASS: rejected scalar helper fault {name}', flush=True)
    print(f'SLOT_SCALAR_FAULTS_DONE planted={len(FAULTS)} rejected={len(FAULTS)}')

if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f'FAIL: scalar helper faults: {exc}', file=sys.stderr)
        sys.exit(1)
