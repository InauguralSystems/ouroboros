#!/usr/bin/env bash
# Ownership A/B companion to the leak tier. Run after build.sh has populated
# build/asan/libeigsrt.a, with the same EIGS_DIR and AOT_ARCH. The seven-tier
# driver checks the runtime pin. Both arms include the CURRENT aot_rt.h;
# SLOT_INDEX_BASELINE selects the original consuming helpers in the C fixture.
# There is no ledger or slack: the candidate may not add leaked allocations.
set -euo pipefail
exec python3 - "$(dirname "$0")/.." "$@" <<'PY'
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile


class GateError(Exception):
    pass


class Cancelled(BaseException):
    pass


pending_signal = None
spawning = False
cleaning = False


def cancel(signum, _frame):
    global pending_signal
    pending_signal = pending_signal or signum
    if not spawning and not cleaning:
        raise Cancelled(f"interrupted by signal {pending_signal}")


for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
    signal.signal(signum, cancel)


def run(command, label, directory, env):
    """One bounded process group; retain exact output, commands and status."""
    global spawning, cleaning
    (directory / f"{label}.command.json").write_text(json.dumps(command) + "\n")
    child = None
    try:
        with (directory / f"{label}.stdout").open("wb") as stdout, \
                (directory / f"{label}.stderr").open("wb") as stderr:
            try:
                spawning = True
                child = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                         env=env, start_new_session=True)
            finally:
                spawning = False
            if pending_signal:
                raise Cancelled(f"interrupted by signal {pending_signal}")
            try:
                return child.wait(timeout=60)
            except subprocess.TimeoutExpired as exc:
                raise GateError(f"{label}: exceeded 60 seconds") from exc
    finally:
        cleaning = True
        try:
            if child is not None:
                # gcc and this direct C fixture do not create new sessions or
                # process groups. Kill any remaining owned group before cleanup.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait(timeout=5)
                (directory / f"{label}.rc").write_text(str(child.returncode) + "\n")
        finally:
            cleaning = False
        if pending_signal:
            raise Cancelled(f"interrupted by signal {pending_signal}")


WITNESS = b"slot index: 300 alias cycles; heap/buffer/arena boundaries OK; scalar consumers OK\n"
HEADER = re.compile(r"==[0-9]+==ERROR: LeakSanitizer: detected memory leaks")
BLOCK = re.compile(r"(?:Direct|Indirect) leak of ([1-9][0-9]*) byte\(s\) in ([1-9][0-9]*) object\(s\) allocated from:")
FRAME = re.compile(r"\s+#[0-9]+\s+0x[0-9a-fA-F]+\b.*")
SUMMARY = re.compile(r"SUMMARY: AddressSanitizer: ([1-9][0-9]*) byte\(s\) leaked in ([1-9][0-9]*) allocation\(s\)\.")


def allocations(arm, rc, directory):
    stdout = (directory / f"{arm}.stdout").read_bytes()
    stderr = (directory / f"{arm}.stderr").read_bytes()
    if stdout != WITNESS:
        raise GateError(f"{arm}: missing, incomplete or changed completion witness")
    if rc == 0 and stderr == b"":
        return 0
    if rc != 1:
        raise GateError(f"{arm}: abnormal exit {rc} or unexpected diagnostics")
    # Accept only a complete LSan report, including its allocation blocks and
    # stack frames. Any ASan/UB/assertion/fatal/extra diagnostic is rejected.
    lines = [line for line in stderr.decode("utf-8", errors="strict").splitlines() if line]
    if lines and re.fullmatch(r"=+", lines[0]):
        lines.pop(0)
    if len(lines) < 4 or not HEADER.fullmatch(lines[0]):
        raise GateError(f"{arm}: exit 1 without a complete LeakSanitizer report")
    summary = SUMMARY.fullmatch(lines[-1])
    if summary is None:
        raise GateError(f"{arm}: missing LSan summary or trailing error diagnostics")
    total_bytes = total_allocations = 0
    frames = None
    for line in lines[1:-1]:
        block = BLOCK.fullmatch(line)
        if block:
            if frames == 0:
                raise GateError(f"{arm}: LSan allocation block has no stack frames")
            total_bytes += int(block[1])
            total_allocations += int(block[2])
            frames = 0
        elif frames is not None and FRAME.fullmatch(line):
            frames += 1
        else:
            raise GateError(f"{arm}: non-LSan or malformed diagnostic: {line}")
    if not frames or (total_bytes, total_allocations) != tuple(map(int, summary.groups())):
        raise GateError(f"{arm}: incomplete or inconsistent LSan allocation population")
    return total_allocations


directory = None
try:
    if len(sys.argv) != 2:
        raise GateError("unexpected arguments; configure EIGS_DIR and AOT_ARCH as for build.sh")
    aot = Path(sys.argv[1]).resolve(strict=True)
    runtime = Path(os.environ.get("EIGS_DIR", "../../EigenScript"))
    if not runtime.is_absolute():
        runtime = aot / runtime
    runtime = runtime.resolve(strict=True)
    fixture = aot / "test/slot_index_ownership.c"
    archive = aot / "build/asan/libeigsrt.a"
    for path in (fixture, archive, aot / "aot_rt.h", runtime / "src/eigenscript.h"):
        if not path.is_file() or path.stat().st_size == 0:
            raise GateError(f"missing or empty input: {path}")
    directory = Path(tempfile.mkdtemp(prefix="aot-slot-index-ownership-"))
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=1:halt_on_error=1:abort_on_error=0:exitcode=1:color=never",
               LSAN_OPTIONS="exitcode=1", UBSAN_OPTIONS="halt_on_error=1:print_stacktrace=1")
    # Same ASan lane as build.sh. -UNDEBUG ensures the fixture's assertions
    # remain active even if a caller accidentally supplies -DNDEBUG in AOT_ARCH.
    common = ["gcc", "-O1", "-g", "-fno-omit-frame-pointer", "-fsanitize=address",
              "-ffp-contract=off", *shlex.split(os.environ.get("AOT_ARCH") or "-march=native"),
              "-UNDEBUG", "-DEIGENSCRIPT_EXT_HTTP=0", "-DEIGENSCRIPT_EXT_MODEL=0",
              "-DEIGENSCRIPT_EXT_DB=0", '-DEIGENSCRIPT_VERSION="aot"',
              f'-DAOT_SCRIPT_DIR="{fixture.parent}"', f'-DAOT_EXE_DIR="{runtime / "src"}"',
              "-I" + str(aot), "-I" + str(runtime / "src")]
    counts = {}
    for arm in ("baseline", "candidate"):
        binary = directory / arm
        command = common + (["-DSLOT_INDEX_BASELINE"] if arm == "baseline" else [])
        command += [str(fixture), str(archive), "-lm", "-lpthread", "-o", str(binary)]
        rc = run(command, arm + ".build", directory, env)
        if rc != 0:
            raise GateError(f"{arm}: ASan compile failed (exit {rc})")
        rc = run([str(binary)], arm, directory, env)
        counts[arm] = allocations(arm, rc, directory)
        print(f"slot index ownership: {arm} witness=1 allocations={counts[arm]} exit={rc}", flush=True)
    if counts["candidate"] > counts["baseline"]:
        raise GateError(f"candidate leaked {counts['candidate']} allocations; baseline leaked {counts['baseline']}")
    print(f"PASS: slot index ownership arms=2 witnesses=2 baseline_allocations={counts['baseline']} candidate_allocations={counts['candidate']} (no slack)")
except (Exception, Cancelled) as exc:
    print(f"FAIL: slot index ownership: {exc}", file=sys.stderr)
    if directory is not None:
        (directory / "failure.txt").write_text(str(exc) + "\n")
        print(f"Diagnostics retained: {directory}", file=sys.stderr)
    sys.exit(1)
else:
    shutil.rmtree(directory)
PY
