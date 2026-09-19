#!/bin/bash
# aot/core_check.sh -- guard build.sh's CORE list against upstream drift
# (ouroboros#90). CORE must equal upstream's Makefile SOURCES minus CLI_ONLY;
# it drifted twice, and each time every AOT program failed to LINK on an
# undefined reference with nothing naming the missing TU (a missing TU is
# latent until something references it -- lint_host was missing for a
# release with no symptom, found by this check on its first run). This diffs the two
# sets by name and fails by name. EIGS_DIR points at the pinned checkout.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
EIGS_DIR="${EIGS_DIR:-$HERE/../../EigenScript}"
MK="$EIGS_DIR/Makefile"
[ -f "$MK" ] || { echo "core_check: no Makefile at $MK (set EIGS_DIR)"; exit 2; }
# WHAT THIS NOW CHECKS, AND WHY IT CHANGED (ouroboros#232).
#
# There is no hand-written CORE any more. build.sh DERIVES it from the
# Makefile of the runtime it is about to link against -- SOURCES minus
# CLI_ONLY, read via `make -pqRr`. So the drift this file was written to
# catch cannot happen: there is no second list to diverge.
#
# That also fixed a bug this gate could not have caught, because the gate
# had it too. A hand list can only be right for ONE runtime, and there are
# two: CI links the PIN (v0.43.0 -- 20 TUs, no builtins_buf/fsutil/task)
# while a developer's sibling checkout is main (23 TUs). Any single list is
# wrong somewhere, and adding the three names turned a green CI red.
#
# So the gate's job is now to keep the DERIVATION honest:
#   1. build.sh must not reintroduce a literal list;
#   2. the derivation must be non-vacuous against the configured EIGS_DIR;
#   3. CLI_ONLY must still be the five TUs the exclusion assumes.
fail_n=0

if grep -qE '^CORE="[a-z_]' "$HERE/build.sh"; then
    echo "core_check: build.sh has a LITERAL CORE list again -- it drifted four times as a literal and must stay derived from \$EIGS_DIR/Makefile"
    fail_n=1
fi
if ! grep -q 'mk_tus SOURCES' "$HERE/build.sh"; then
    echo "core_check: build.sh no longer derives CORE from SOURCES -- the derivation is the mechanism, not a convenience"
    fail_n=1
fi

mk_tus() {  # mirrors build.sh; `make -q` exits 1 normally, so tolerate it
    local db line
    db=$(make -C "$EIGS_DIR" -pqRr 2>/dev/null || true)
    line=$(printf '%s\n' "$db" | awk -v v="$1" '$1==v && ($2==":=" || $2=="=") {print; exit}')
    printf '%s\n' "$line" | tr ' ' '\n' |
        sed -nE 's#^(\$\(SRC_DIR\)|[a-z_0-9]+)/([a-z_0-9]+)\.c$#\2#p' | sort -u
}
srcs=$(mk_tus SOURCES); cli=$(mk_tus CLI_ONLY)
n_src=$(printf '%s\n' "$srcs" | grep -c .)
n_cli=$(printf '%s\n' "$cli" | grep -c .)
core=$(comm -23 <(printf '%s\n' "$srcs") <(printf '%s\n' "$cli"))
n_core=$(printf '%s\n' "$core" | grep -c .)

# Vacuity, both sides (section 121). The historical bug produced a SHORT
# list, not an empty one, so "non-empty" would not have caught it.
if [ "$n_src" -lt 20 ]; then
    echo "core_check: read only $n_src TU(s) from $EIGS_DIR SOURCES (floor 20) -- the extraction is broken, not the tree"
    fail_n=1
fi
if [ "$n_cli" -ne 5 ]; then
    echo "core_check: CLI_ONLY has $n_cli TU(s), expected 5 -- upstream changed the CLI split; confirm before trusting the exclusion"
    fail_n=1
fi
# A TU that the runtime builds but the derivation drops would be invisible
# otherwise: every SOURCES entry is either in CORE or in CLI_ONLY, nothing
# may fall between them.
lost=$(comm -23 <(printf '%s\n' "$srcs") <(cat <(printf '%s\n' "$core") <(printf '%s\n' "$cli") | sort -u))
if [ -n "$lost" ]; then
    echo "core_check: TU(s) in SOURCES reached neither CORE nor CLI_ONLY: $(echo $lost)"
    fail_n=1
fi

if [ "$fail_n" = 0 ]; then
    echo "core_check: build.sh derives CORE from $EIGS_DIR ($n_core TUs = SOURCES $n_src - CLI_ONLY $n_cli), no literal list"
    exit 0
fi
exit 1
