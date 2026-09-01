#!/bin/bash
# RUN-differential over the EigenScript test corpus: every tests/test_*.eigs
# executed under the VM and under the AOT binary, outputs and exit codes
# compared, and the set of non-matching programs diffed against a committed
# baseline.
#
# Why this exists (round 105): stdlib_sweep.sh proves modules BUILD, and
# lib/math.eigs builds fine while its documented `dot` printed 0 instead of
# 32 -- a user function shadowing a builtin name was silently discarded.
# **A build sweep cannot see a wrong ANSWER.** That needs a differential RUN
# over real programs, which is what this is: 218 drivers someone already
# wrote, against the byte-exact oracle.
#
# The baseline is a LEDGER, not an amnesty. Every REFUSE line should be a
# bar-clause-2 refusal naming a real reason, and every DIVERGE line should
# map to a filed issue. Entries are meant to be worked DOWN; a shrinking
# baseline is the loop making progress, and the script reports improvements
# separately from regressions so neither is absorbed silently.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
EIGDIR="${EIGS_ROOT:-$HERE/../../EigenScript}"
BASE="$HERE/test/canary/corpus_expected.txt"
EIG="$EIGDIR/src/eigenscript"
[ -x "$EIG" ] || { echo "corpus: no VM at $EIG (set EIGS_ROOT)"; exit 2; }

norm() { grep -vE '^\s+[0-9]+ \||^\s+\|.*\^|^  at ' "$1" | sed -E 's/^Error line [0-9]+:/Error line:/'; }
got=$(mktemp); n=0
for f in "$EIGDIR"/tests/test_*.eigs; do
  n=$((n + 1)); b=$(basename "$f")
  ( cd "$EIGDIR" && timeout 60 src/eigenscript "tests/$b" ) >/tmp/_cd_vm.out 2>&1
  vrc=$?
  if timeout 240 bash "$HERE/build.sh" "$f" /tmp/_cd_bin >/tmp/_cd_b.log 2>&1; then
    ( cd "$EIGDIR" && timeout 60 /tmp/_cd_bin ) >/tmp/_cd_aot.out 2>&1
    brc=$?
    if [ "$vrc" -ne "$brc" ] || ! diff <(norm /tmp/_cd_vm.out) <(norm /tmp/_cd_aot.out) >/dev/null; then
      echo "$b DIVERGE" >>"$got"
    fi
  else
    if grep -q 'AOT:' /tmp/_cd_b.log; then echo "$b REFUSE" >>"$got"
    else echo "$b BUILDCRASH" >>"$got"; fi
  fi
done
touch "$got"; sort -o "$got" "$got"
[ "$n" -gt 0 ] || { echo "corpus: examined ZERO programs -- refusing to pass"; exit 1; }

if [ ! -f "$BASE" ]; then
  echo "corpus: NO BASELINE at $BASE -- refusing to self-baseline."
  echo "corpus: mint one deliberately with:  cp $got $BASE"
  exit 1
fi
if diff "$BASE" "$got" >/dev/null; then
  echo "corpus: $((n - $(wc -l <"$got"))) / $n programs match the VM byte-for-byte; the rest match the ledger"
  rm -f "$got"; exit 0
fi
echo "corpus: LEDGER CHANGED ($n programs examined)"
echo "  '<' = ledgered and now MATCHES (an improvement -- remove it from the baseline)"
echo "  '>' = newly diverging or refusing (a REGRESSION)"
diff "$BASE" "$got"
echo "corpus: observed set kept at $got"
exit 1
