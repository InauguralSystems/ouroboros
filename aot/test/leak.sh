#!/bin/bash
# aot/test/leak.sh -- the LEAK tier (ouroboros#136). Builds every
# test/leak/*.eigs with AOT_SAN=asan (build.sh's AddressSanitizer lane) and
# runs it under LeakSanitizer; the allocation count LSan reports must not
# exceed the fixture's allowance in test/canary/leak_expected.txt (the
# pre-existing shutdown floor, measured per fixture -- #130 records that
# AOT binaries do not free everything at exit). A count BELOW the allowance
# is an improvement and is reported so the ledger can be lowered; a count
# above it is a leak. Validated by planting the two #136 mutants
# (aot_lv_set without its slot_decref; aot_lv_getb without its write-back):
# both must push a fixture over its allowance, or this tier is decoration.
# The floor jitters by one allocation between environments (CI's
# devcontainer measured l1 252 / l2 253 where the dev box measures 253 /
# 252), so a fixture passes within LEAK_SLACK of its row; the mutants leak
# per ITERATION (thousands on 2000-iteration fixtures), which no slack of
# a few allocations can hide.
LEAK_SLACK=${LEAK_SLACK:-8}
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE" || exit 2
LEDGER="test/canary/leak_expected.txt"
fail=0; n=0; improved=0
for prog in test/leak/*.eigs; do
  [ -f "$prog" ] || continue
  name=$(basename "$prog" .eigs); n=$((n + 1))
  bin="/tmp/aot_leak_$$_$name"
  if ! AOT_SAN=asan bash build.sh "$prog" "$bin" > "/tmp/aot_leak_$$_build.log" 2>&1; then
    echo "FAIL: leak $name (asan build failed)"; head -5 "/tmp/aot_leak_$$_build.log"; fail=1; continue
  fi
  out=$( cd test/leak && ASAN_OPTIONS=detect_leaks=1 "$bin" 2>&1 ); rc=$?
  allocs=$(printf '%s\n' "$out" | grep -oE 'leaked in [0-9]+ allocation' | grep -oE '[0-9]+' | head -1)
  allocs=${allocs:-0}
  allow=$(grep -E "^$name " "$LEDGER" 2>/dev/null | awk '{print $2}')
  if printf '%s\n' "$out" | grep -qE 'ERROR: AddressSanitizer: (heap|stack|global|use|double)'; then
    echo "FAIL: leak $name (AddressSanitizer error, not a leak)"; printf '%s\n' "$out" | grep -m1 'ERROR: AddressSanitizer'; fail=1
  elif [ -z "$allow" ]; then
    echo "FAIL: leak $name has no allowance row in $LEDGER (measured: $allocs allocation(s))"; fail=1
  elif [ "$allocs" -gt $((allow + LEAK_SLACK)) ]; then
    echo "FAIL: leak $name leaked $allocs allocation(s), allowance $allow (+$LEAK_SLACK slack)"; fail=1
  elif [ "$allocs" -lt $((allow - LEAK_SLACK)) ]; then
    echo "PASS: leak $name ($allocs allocation(s), well under allowance $allow -- lower the ledger)"; improved=1
  else
    echo "PASS: leak $name ($allocs allocation(s), allowance $allow)"
  fi
  rm -f "$bin"
done
rm -f "/tmp/aot_leak_$$_build.log"
echo "--- leak tier: $n fixture(s) ---"
[ "$fail" -eq 0 ] || exit 1
exit 0
