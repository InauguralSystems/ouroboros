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
  ref=$("$EIG" "$prog" 2>&1)
  got=$("$bin" 2>&1)
  match=0
  case "$name" in
    *_tol*)
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
    *_err*)
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
  if [ "$match" -eq 1 ]; then
    echo "PASS: $name"
  else
    echo "FAIL: $name"
    echo "  VM:  $(printf '%s' "$ref" | tr '\n' '|')"
    echo "  AOT: $(printf '%s' "$got" | tr '\n' '|')"
    fail=1
  fi
  rm -f "$bin"
done
[ "$fail" -eq 0 ] && echo "--- all AOT parity tests passed ---" || exit 1
