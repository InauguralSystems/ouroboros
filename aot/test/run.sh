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
