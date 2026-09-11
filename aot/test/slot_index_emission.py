#!/usr/bin/env python3
"""Require the slot-index fixtures to exercise the intended emitter paths.

Transpile only: no gcc and no fixture execution. The ordinary AOT suite owns
VM parity; this check prevents an unused optimization from passing that suite.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


FIXTURES = {
    "t337_slot_index_locals.eigs": "all",
    "t338_slot_index_callbacks.eigs": "none",
    "t339_slot_index_traced.eigs": "none",
    "t340_slot_index_errors.eigs": "any",
    "t341_slot_index_ownership.eigs": "any",
    "t342_slot_index_observed.eigs": "none",
}


def require_input(path):
    if not path.is_file() or not path.read_bytes().strip():
        raise ValueError(f"missing or empty regular input: {path}")


def check_emission(name, rc, output, stderr):
    if rc != 0 or stderr:
        raise ValueError(f"{name}: transpile failed/refused (exit {rc}, stderr={len(stderr)} bytes)")
    # Inspect generated call expressions, excluding comments/string literals
    # and declarations. The runtime header is included, never preprocessed here.
    code = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                  ' ', output.decode("utf-8"), flags=re.S)
    if not re.search(r"\bint\s+main\s*\(", code):
        raise ValueError(f"{name}: empty or refused C emission (no main)")
    calls = re.findall(r"\baot_lv_index_([siv])\s*\(\s*&", code)
    counts = {suffix: calls.count(suffix) for suffix in "siv"}
    mode = FIXTURES[name]
    valid = all(counts.values()) if mode == "all" else bool(calls) if mode == "any" else not calls
    if not valid:
        raise ValueError(f"{name}: expected {mode} slot-index call paths, got {counts}")
    return counts


def selftest():
    cases = 0

    def reject(function, *args):
        nonlocal cases
        try:
            function(*args)
        except (ValueError, UnicodeError):
            cases += 1
        else:
            raise AssertionError(f"planted fault was accepted: {args}")

    name = next(iter(FIXTURES))
    full = b"int main(void) { aot_lv_index_s(&x,t,i); aot_lv_index_i(&x,t,0); aot_lv_index_v(&x,t,i); }"
    plain = b"int main(void) { return 0; }"
    with tempfile.TemporaryDirectory(prefix="slot-emission-selftest-") as temp:
        source = Path(temp) / "fixture.eigs"
        reject(require_input, source)
        source.write_bytes(b"")
        reject(require_input, source)
        source.write_bytes(b"print of 1\n")
        require_input(source)
        cases += 1
    for fixture, mode in FIXTURES.items():
        check_emission(fixture, 0, plain if mode == "none" else full, b"")
        cases += 1
        reject(check_emission, fixture, 0, full if mode == "none" else plain, b"")
    reject(check_emission, name, 9, full, b"")
    reject(check_emission, name, 0, full, b"fake transpile refusal")
    reject(check_emission, name, 0, b"", b"")
    reject(check_emission, name, 0, full.replace(b"aot_lv_index_v", b"old_helper"), b"")
    reject(check_emission, name, 0, plain + b"/* aot_lv_index_s(&x,t,i); */", b"")
    print(f"PASS: slot index emission selftests cases={cases}")


def main():
    if sys.argv[1:] == ["--selftest"]:
        selftest()
        return
    if sys.argv[1:]:
        raise ValueError("usage: slot_index_emission.py [--selftest]; configure EIGS/EIGS_DIR")
    aot = Path(__file__).resolve().parents[1]
    runtime = Path(os.environ.get("EIGS_DIR", "../../EigenScript"))
    runtime = (aot / runtime).resolve()
    eig = os.environ.get("EIGS", str(runtime / "src/eigenscript"))
    if "/" in eig:
        eig = str((aot / eig).resolve())
    compiler = aot / "compile.eigs"
    require_input(compiler)
    for name in FIXTURES:
        require_input(aot / "test" / name)
    artifacts = Path(tempfile.mkdtemp(prefix="aot-slot-index-emission-"))
    completed = 0
    try:
        for name in FIXTURES:
            command = [eig, str(compiler), str(aot / "test" / name), str(runtime)]
            # The VM is a direct child; subprocess.run kills/reaps it on timeout.
            result = subprocess.run(command, cwd=aot.parent, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, timeout=60)
            (artifacts / (name + ".c")).write_bytes(result.stdout)
            (artifacts / (name + ".stderr")).write_bytes(result.stderr)
            (artifacts / (name + ".rc")).write_text(str(result.returncode) + "\n")
            counts = check_emission(name, result.returncode, result.stdout, result.stderr)
            completed += 1
            print(f"PASS: slot index emission {name} s={counts['s']} i={counts['i']} v={counts['v']}", flush=True)
    except BaseException:
        print(f"slot index emission: completed={completed}/6; diagnostics retained: {artifacts}", file=sys.stderr)
        raise
    else:
        shutil.rmtree(artifacts)
    print(f"SLOT_INDEX_EMISSION_DONE fixtures={completed} checks={completed}")


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt) as exc:
        print(f"FAIL: slot index emission: {exc}", file=sys.stderr)
        sys.exit(1)
