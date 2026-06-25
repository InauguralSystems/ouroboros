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
  if [ "$ref" = "$got" ]; then
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
