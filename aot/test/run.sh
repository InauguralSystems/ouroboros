#!/bin/bash
# AOT differential harness: native output must equal the VM oracle, byte-for-byte.
set -uo pipefail
cd "$(dirname "$0")/.."
EIG="${EIGS:-../../EigenScript/src/eigenscript}"
fail=0
for prog in test/*.eigs; do
  name=$(basename "$prog")
  bin=$(mktemp /tmp/aot_test.XXXXXX)
  if ! bash build.sh "$prog" "$bin" >/tmp/aot_build.log 2>&1; then
    echo "BUILD FAIL: $name"; tail -3 /tmp/aot_build.log; fail=1; rm -f "$bin"; continue
  fi
  ref=$("$EIG" "$prog" 2>&1); ref_rc=$?
  got=$("$bin" 2>&1); got_rc=$?
  match=0
  why=""
  case "$name" in
    *_tol.eigs)
      # Association-unspecified reductions (dot): the AOT reassociates the sum
      # across SIMD lanes, so it agrees with the VM within tolerance, NOT byte
      # for byte (FP add is non-associative — by design, per the `dot` spec).
      VM="$ref" AOT="$got" python3 - <<'PY' && match=1
import os, re, sys
def nums(s): return re.findall(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?', s)
a, b = nums(os.environ['VM']), nums(os.environ['AOT'])
if len(a) != len(b): sys.exit(1)
for x, y in zip(a, b):
    fx, fy = float(x), float(y)
    if abs(fx - fy) > 1e-9 * max(1.0, abs(fx)): sys.exit(1)
sys.exit(0)
PY
      ;;
    *_err.eigs)
      # Programs that RAISE. The AOT cannot reproduce the VM's uncaught-error
      # diagnostic: the source excerpt + column caret are added by the VM's
      # CHECK_ERROR from the failing instruction's bytecode offset (#407), and
      # the `at <frame>` trace comes from vm_print_stack_trace walking VM
      # frames — neither exists in a native binary. The AOT also has no line
      # info unless the program is traced (the emitter stamps
      # g_trace_current_line only under g_traced), so it reports line 0.
      #
      # What must still match EXACTLY is the part that is semantics rather
      # than presentation: everything the program printed before it died, and
      # the error's kind and message text. That is the property this class
      # exists to defend — an AOT that RUNS PAST a line the VM stops at is the
      # silent-wrong-answer bug (ouroboros#96), and it is caught here.
      #
      # Normalization: drop the VM-only excerpt/caret/`at ...` lines and blank
      # the line number in the `Error line N:` frame. Nothing else is touched,
      # so a divergence in message text, error kind, ordering, or any stdout
      # line still fails. Diagnostic parity itself is tracked separately —
      # when the AOT can site its errors, delete this class and re-diff.
      norm() {
        printf '%s\n' "$1" \
          | grep -vE '^\s+[0-9]+ \||^\s+\|.*\^|^  at ' \
          | sed -E 's/^Error line [0-9]+:/Error line:/'
      }
      [ "$(norm "$ref")" = "$(norm "$got")" ] && match=1
      ;;
    *) [ "$ref" = "$got" ] && match=1 ;;
  esac
  # Exit-code contract (#101): text alone is spoofable — a VM-errors/AOT-
  # exits-0 pair with matching normalized text sailed through here, which is
  # the run-past-error class this suite exists to catch. Normal and _tol
  # cases need a CLEAN nonempty reference and equal exit codes; _err cases
  # need BOTH sides to die (a VM that starts exiting 0 means the class label
  # is stale — fail loud, don't quietly re-purpose the normalizer). The class
  # markers are filename SUFFIXES (`*_err.eigs`), not substrings: the old
  # `*_err*` glob silently put t59_sited_errors — a clean rc-0 program — in
  # the raising class, where the normalizer could have eaten a real
  # divergence. Since #103 the _err death codes are compared for EQUALITY:
  # the AOT dies by a clean exit(1) on every uncaught runtime error (the old
  # SIGSEGV-after-the-message rc-139 teardown was a NULL-VM deref inside
  # rt_error's stack-trace walk, fixed in aot_rt.h), which is exactly the
  # VM's uncaught-error exit code.
  if [ "$match" -eq 1 ]; then
    case "$name" in
      *_err.eigs)
        if [ "$ref_rc" -eq 0 ]; then
          match=0; why="_err case but the VM exits 0 — stale class, drop the suffix"
        elif [ "$got_rc" -eq 0 ]; then
          match=0; why="VM errors (rc=$ref_rc) but the AOT exits 0 — runs past the error"
        elif [ "$got_rc" -ne "$ref_rc" ]; then
          match=0; why="death codes differ (VM $ref_rc, AOT $got_rc) — #103 contract: uncaught errors exit cleanly with the VM's code"
        fi ;;
      *)
        if [ "$ref_rc" -ne 0 ]; then
          match=0; why="VM reference errors (rc=$ref_rc) — declare *_err.eigs or fix the program"
        elif [ -z "$ref" ]; then
          match=0; why="VM reference output is empty — a parity claim needs a nonempty reference"
        elif [ "$got_rc" -ne "$ref_rc" ]; then
          match=0; why="exit codes differ (VM $ref_rc, AOT $got_rc)"
        fi ;;
    esac
  fi
  if [ "$match" -eq 1 ]; then
    echo "PASS: $name"
  else
    echo "FAIL: $name${why:+ ($why)}"
    echo "  VM:  $(printf '%s' "$ref" | tr '\n' '|')"
    echo "  AOT: $(printf '%s' "$got" | tr '\n' '|')"
    fail=1
  fi
  rm -f "$bin"
done
[ "$fail" -eq 0 ] && echo "--- all AOT parity tests passed ---" || exit 1
