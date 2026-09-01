#!/bin/bash
# Build EVERY module of the real EigenScript standard library and compare the
# set of modules that fail against a committed baseline.
#
# Why this exists (round 103): the fixture suite was green while NINE of the
# 75 stdlib modules could not be compiled at all -- an uncalled function's
# params default numeric, so `"msg: " + key` tripped an internal emit_num
# assertion and no C was emitted. Every one of those modules is ordinary
# EigenScript that the VM runs. The suite could not see it because a fixture
# is a program someone thought to write, and this class is about programs
# nobody calls: a LIBRARY's whole purpose is functions its own file never
# invokes. 51/75 -> 65/77 in one fix.
#
# The baseline is a set, not a count, so it catches BOTH directions: a module
# that starts failing is a regression, and a module that starts BUILDING is a
# deliberate improvement that must be recorded (same discipline as the DMG
# canary -- an instrument that silently re-baselines is not an instrument).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
LIB="${LIB_DIR:-$HERE/../../EigenScript/lib}"
BASE="$HERE/test/canary/stdlib_expected_failures.txt"
[ -d "$LIB" ] || { echo "sweep: no stdlib at $LIB (set LIB_DIR)"; exit 2; }

got=$(mktemp)
n=0
for f in "$LIB"/*.eigs; do
  n=$((n + 1))
  out=$(mktemp)
  if ! timeout 240 bash "$HERE/build.sh" "$f" /tmp/_sweepbin >"$out" 2>&1; then
    if grep -q 'AOT:' "$out"; then
      echo "$(basename "$f") REFUSE" >>"$got"
    else
      echo "$(basename "$f") CRASH" >>"$got"
    fi
  fi
  rm -f "$out"
done
sort -o "$got" "$got"
[ "$n" -gt 0 ] || { echo "sweep: examined ZERO modules -- refusing to pass"; exit 1; }

if [ ! -f "$BASE" ]; then
  echo "sweep: NO BASELINE at $BASE -- refusing to self-baseline."
  echo "sweep: mint one deliberately with:  cp $got $BASE"
  exit 1
fi
if diff "$BASE" "$got" >/dev/null; then
  echo "sweep: $((n - $(wc -l <"$got"))) / $n modules build; failures match the baseline"
  rm -f "$got"; exit 0
fi
echo "sweep: FAILURE SET CHANGED ($n modules examined)"
echo "  '<' = expected to fail and now BUILDS (an improvement -- update the baseline)"
echo "  '>' = newly failing (a REGRESSION)"
diff "$BASE" "$got"
echo "sweep: observed set kept at $got"
exit 1
