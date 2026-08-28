#!/usr/bin/env bash
# AOT ENVELOPE CENSUS — which ecosystem consumers can the AOT compile at all?
#
# The consumers were built to stress the LANGUAGE, and they did: each one bought
# runtime fixes. Pointing them at the AOT is a different question with a
# different failure mode. A language gap shows up as a wrong answer, which a
# test catches. An ENVELOPE gap shows up as a refusal to compile, which nothing
# catches, because a program that was never compiled has no failing test.
#
# So the envelope needs an instrument before it needs fixes. This is it: it
# TRANSPILES each consumer entry point (no gcc, no linking, seconds per
# program) and reports whether the AOT accepts it and, if not, the first
# refusal. That is enough to see the envelope move.
#
# Bought 2026-08-28: the first run found 8 of 8 consumers refused, each for a
# different reason, and nobody knew — DMG was the only non-trivial program the
# AOT could compile, and only since #129. One refusal (liferaft) turned out to
# be a real second-parser divergence from the canonical parser (#138), sitting
# unnoticed because no fixture in either suite used a bare `return`.
#
# Usage:  bash aot/tools/envelope_census.sh [ECOSYSTEM_DIR]
# Exit:   0 always — this is a census, not a gate. Read the table.
set -Eeuo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
EIGS_SRC="${EIGS_DIR:-$ROOT/EigenScript}"
EIG="${EIGS:-$EIGS_SRC/src/eigenscript}"
COMPILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/compile.eigs"
TIMEOUT="${ENVELOPE_TIMEOUT:-180}"

[ -x "$EIG" ] || { echo "ERROR: no eigenscript binary at $EIG (set EIGS=)"; exit 0; }

# Entry points, one per consumer. A consumer with several is listed several
# times: the envelope is per-PROGRAM, not per-repo.
PROGRAMS="
DMG/dmg.eigs
EigenMiniSat/minisat.eigs
EigenRegex/regex.eigs
liferaft/liferaft.eigs
tidelog/tidelog.eigs
dynamics/dynamics.eigs
eddy/explorer.eigs
polymethod/polymethod.eigs
Tidepool/eval_policy.eigs
"

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
ok=0; refused=0; missing=0

printf '%-28s %-9s %s\n' PROGRAM VERDICT "FIRST REFUSAL"
printf '%-28s %-9s %s\n' "----------------------------" "-------" "-------------"
for p in $PROGRAMS; do
  f="$ROOT/$p"
  if [ ! -f "$f" ]; then
    printf '%-28s %-9s %s\n' "$p" "-" "(no such file — consumer not cloned?)"
    missing=$((missing+1)); continue
  fi
  if EIGS_DIR="$EIGS_SRC" timeout "$TIMEOUT" "$EIG" "$COMPILE" "$f" >/dev/null 2>"$tmp/err"; then
    printf '%-28s %-9s\n' "$p" "COMPILES"
    ok=$((ok+1))
  else
    # first NON-EMPTY line is the throw text; some refusals lead with a blank
    # line, which silently produced "(empty diagnostic)" on the first run.
    why=$(grep -m1 -v '^[[:space:]]*$' "$tmp/err" | sed -E 's/^AOT: //' | cut -c1-64)
    [ -z "$why" ] && why="(timeout or empty diagnostic)"
    printf '%-28s %-9s %s\n' "$p" "REFUSED" "$why"
    refused=$((refused+1))
  fi
done

echo
echo "compiles: $ok   refused: $refused   not present: $missing"
echo
echo "A refusal is a LOUD, correct answer — the AOT proves the subset it claims"
echo "(F-OURO-23). The number worth watching is how the ratio moves over time:"
echo "every consumer admitted is a new workload stressing the compiler, and the"
echo "AOT's speed work is only reachable by programs it will accept."
