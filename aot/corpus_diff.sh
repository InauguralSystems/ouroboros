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
CDT="/tmp/_cd_$$"
trap 'rm -f "$CDT".* 2>/dev/null' EXIT
HERE="$(cd "$(dirname "$0")" && pwd)"
EIGDIR="${EIGS_ROOT:-$HERE/../../EigenScript}"
BASE="$HERE/test/canary/corpus_expected.txt"
EIG="$EIGDIR/src/eigenscript"
[ -x "$EIG" ] || { echo "corpus: no VM at $EIG (set EIGS_ROOT)"; exit 2; }

norm() { grep -vE '^\s+[0-9]+ \||^\s+\|.*\^|^  at ' "$1" | sed -E 's/^Error line [0-9]+:/Error line:/'; }
got=$(mktemp); n=0
# (round 113) CDT (PID-scoped scratch, set at top with its cleanup trap)
# replaced fixed /tmp/_cd_* names: a CONCURRENT corpus run -- a sibling
# session's gate sharing this box -- stomped those files mid-comparison and
# produced 60 phantom DIVERGE rows, a FALSE red that refused a verified
# commit. Every flagged program matched when re-run alone. An instrument a
# neighbour can corrupt is not an instrument.
for f in "$EIGDIR"/tests/test_*.eigs; do
  n=$((n + 1)); b=$(basename "$f")
  ( cd "$EIGDIR" && timeout 60 src/eigenscript "tests/$b" ) >$CDT.vm 2>&1
  vrc=$?
  timeout 240 bash "$HERE/build.sh" "$f" $CDT.bin >$CDT.blog 2>&1
  bst=$?
  if [ "$bst" -eq 0 ]; then
    ( cd "$EIGDIR" && timeout 60 $CDT.bin ) >$CDT.aot 2>&1
    brc=$?
    if [ "$vrc" -ne "$brc" ] || ! diff <(norm $CDT.vm) <(norm $CDT.aot) >/dev/null; then
      echo "$b DIVERGE" >>"$got"
    fi
  elif [ "$bst" -eq 124 ]; then
    # (round 106) a TIMEOUT is not a crash: test_stmt_cap is an 8424-line
    # program and the compiler is superlinear in statement count (~n^1.8
    # measured), so it blows the budget without ever failing. Conflating
    # the two under BUILDCRASH hid the perf finding behind a severity it
    # does not have.
    echo "$b TIMEOUT" >>"$got"
  else
    if grep -q 'AOT:' $CDT.blog; then echo "$b REFUSE" >>"$got"
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
# (round 170) NONDET rows: a program whose AOT outcome is TIMING-DEPENDENT
# (spawn-based drivers under #188: the same binary matched once in a gate,
# then mismatched twice and segfaulted once when re-run three times) is
# excluded from BOTH sides of the comparison. Without this class a lucky
# run reads as "ledgered and now MATCHES" and the tier goes red on an
# improvement that does not exist; the next run then flips it back. A
# NONDET row is still a ledger entry to be worked down -- it names the
# issue that makes the outcome nondeterministic, and the row leaves when
# the outcome becomes deterministic (MATCH every run).
nondet=$(awk '$2=="NONDET"{print $1}' "$BASE")
basecmp=$(mktemp); gotcmp=$(mktemp)
awk '$2!="NONDET"' "$BASE" >"$basecmp"
if [ -n "$nondet" ]; then
  grep -vFx -f <(for x in $nondet; do echo "$x DIVERGE"; echo "$x REFUSE"; echo "$x BUILDCRASH"; echo "$x TIMEOUT"; done) "$got" >"$gotcmp" || true
else
  cp "$got" "$gotcmp"
fi
if diff "$basecmp" "$gotcmp" >/dev/null; then
  echo "corpus: $((n - $(wc -l <"$got"))) / $n programs match the VM byte-for-byte; the rest match the ledger ($(echo $nondet | wc -w) NONDET excluded)"
  rm -f "$got" "$basecmp" "$gotcmp"; exit 0
fi
rm -f "$basecmp" "$gotcmp"
echo "corpus: LEDGER CHANGED ($n programs examined)"
echo "  '<' = ledgered and now MATCHES (an improvement -- remove it from the baseline)"
echo "  '>' = newly diverging or refusing (a REGRESSION)"
diff <(awk '$2!="NONDET"' "$BASE") "$got" | grep -vF -f <(for x in $nondet; do echo "$x "; done) || true
# (round 184) the kept set is what a `cp` onto the ledger would install, so it
# must carry the base's NONDET rows in place of the run's outcome for those
# programs: round 181 copied a raw observed set and silently turned
# test_spawn_parallel's NONDET back into DIVERGE, which the next gate then
# flagged as an "improvement".
if [ -n "$nondet" ]; then
  keep=$(mktemp)
  grep -vFx -f <(for x in $nondet; do echo "$x DIVERGE"; echo "$x REFUSE"; echo "$x BUILDCRASH"; echo "$x TIMEOUT"; done) "$got" > "$keep" || true
  for x in $nondet; do echo "$x NONDET" >> "$keep"; done
  sort -o "$got" "$keep"; rm -f "$keep"
fi
echo "corpus: observed set kept at $got (NONDET rows carried from the ledger; safe to cp)"
exit 1
