#!/usr/bin/env bash
# AOT ENVELOPE CENSUS — which ecosystem programs can the AOT compile at all?
#
# The consumers were built to stress the LANGUAGE, and they did: each one bought
# runtime fixes. Pointing them at the AOT is a different question with a
# different failure mode. A language gap shows up as a wrong answer, which a
# test catches. An ENVELOPE gap shows up as a refusal to compile, which nothing
# catches, because a program that was never compiled has no failing test.
#
# So the envelope needs an instrument before it needs fixes. This TRANSPILES
# each program (no gcc, no linking) and reports whether the AOT accepts it and,
# if not, the first refusal. That is enough to see the envelope move.
#
# ENTRY POINTS ARE DISCOVERED, NOT LISTED. The first version of this script
# carried a hand-written list of nine programs and was immediately wrong — the
# ecosystem has an order of magnitude more, several repos keep their mains one
# level down (DeslanStudio/src, iLambdaAi/scripts, EigenOS/demos), and a
# hardcoded list silently under-reports exactly the surfaces nobody is looking
# at. Discovery over enumeration; the exclusions below are the only judgement.
#
# Usage:  bash aot/tools/envelope_census.sh [ECOSYSTEM_DIR]
#         CENSUS_DEPTH=3 to widen discovery; CENSUS_TIMEOUT=60 per program.
# Exit:   0 always — this is a census, not a gate. Read the table.
set -Eeuo pipefail

ROOT="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
EIGS_SRC="${EIGS_DIR:-$ROOT/EigenScript}"
EIG="${EIGS:-$EIGS_SRC/src/eigenscript}"
COMPILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/compile.eigs"
TIMEOUT="${CENSUS_TIMEOUT:-60}"
DEPTH="${CENSUS_DEPTH:-2}"

[ -x "$EIG" ] || { echo "ERROR: no eigenscript binary at $EIG (set EIGS=)"; exit 0; }

# Repos that are not AOT targets:
#   EigenScript, ouroboros           — the toolchain itself
#   awesome-*, homebrew-*, *-template — no programs
#   EigenAttic, EigenAttention        — PARKED, do not revive
#   EigenOS                           — freestanding kernel profile, a separate
#                                       compilation target with its own rules
SKIP_REPOS="EigenScript ouroboros awesome-eigenscript homebrew-eigenscript eigs-package-template EigenAttention EigenAttic EigenOS"

# Paths that are not entry points: suites, fixtures, benchmark corpora, and
# vendored stdlib copies.
SKIP_PATH_RE='/(test|tests|bench|benchmarks|fixtures|lib|vendor|node_modules)/'

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
ok=0; refused=0; timedout=0

printf '%-46s %-9s %s\n' PROGRAM VERDICT "FIRST REFUSAL"
printf '%-46s %-9s %s\n' "----------------------------------------------" "--------" "-------------"

for repo in "$ROOT"/*/; do
  r=$(basename "$repo")
  case " $SKIP_REPOS " in *" $r "*) continue;; esac
  [ -d "$repo" ] || continue
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    printf '%s' "$f" | grep -qE "$SKIP_PATH_RE" && continue
    rel="${f#"$ROOT"/}"
    set +e
    EIGS_DIR="$EIGS_SRC" timeout "$TIMEOUT" "$EIG" "$COMPILE" "$f" >/dev/null 2>"$tmp/err"
    rc=$?
    set -e
    if [ $rc -eq 0 ]; then
      printf '%-46s %-9s\n' "$rel" "COMPILES"; ok=$((ok+1))
    elif [ $rc -eq 124 ]; then
      printf '%-46s %-9s %s\n' "$rel" "TIMEOUT" "(> ${TIMEOUT}s — raise CENSUS_TIMEOUT)"; timedout=$((timedout+1))
    else
      why=$(grep -m1 -v '^[[:space:]]*$' "$tmp/err" | sed -E 's/^AOT: //' | cut -c1-58)
      [ -z "$why" ] && why="(no diagnostic)"
      printf '%-46s %-9s %s\n' "$rel" "REFUSED" "$why"; refused=$((refused+1))
    fi
  done < <(find "$repo" -maxdepth "$DEPTH" -name '*.eigs' 2>/dev/null | sort)
done

echo
echo "compiles: $ok   refused: $refused   timeout: $timedout   (total $((ok+refused+timedout)))"
echo
echo "A refusal is a LOUD, correct answer — the AOT proves the subset it claims"
echo "(F-OURO-23). The number worth watching is how the ratio moves, and WHICH"
echo "surfaces the newly-admitted programs stress: every consumer was built to"
echo "stress something different, so a blocker's value is the compiler surface"
echo "it makes testable, not the number of rows it flips."
