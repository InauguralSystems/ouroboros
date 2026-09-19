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
# ASK MAKE, DO NOT GREP ITS SOURCE (mechanical-gates section 1).
#
# This grepped `^SOURCES *:?=` and piped the MATCHING LINE to names(). grep
# returns one line; a backslash continuation is on the next one. names()
# strips backslashes, so continuations had been anticipated -- but grep never
# delivered them. FULL_SOURCES two lines below already wraps, so the style
# was live in the file.
#
# Measured (the phone-side reviewer, 2026-09-19): reformatting SOURCES with
# one line break, changing ZERO TUs, made `task` VANISH from the MISSING list
# while adding fourteen phantom extras. The phantoms are the loud half; the
# quiet half is a genuinely absent TU no longer being reported, from a
# cosmetic edit -- the instrument going silent in the direction that costs
# you. That is the whole failure class this file exists to prevent, living
# inside the file.
#
# `make -pqRr` prints the post-expansion database: continuations joined,
# variables resolved, += applied. There is no fallback to the old parse on
# purpose -- a gate that cannot read its authority must FAIL, not guess.
mk_var() {  # $1 = variable name -> one TU basename per line
    make -C "$EIGS_DIR" -pqRr 2>/dev/null |
        sed -nE "s/^$1 :?= *//p" | head -1 |
        tr ' ' '\n' |
        sed -nE 's#^(\$\(SRC_DIR\)|[a-z_0-9]+)/([a-z_0-9]+)\.c$#\2#p' | sort -u
}
core=$(sed -n '/^CORE="/,/"/p' "$HERE/build.sh" | tr -d '"\\' | sed 's/^CORE=//' | tr ' ' '\n' | grep -E '^[a-z_0-9]+$' | sort -u)
sources=$(mk_var SOURCES)
cli=$(mk_var CLI_ONLY)

# VACUITY, both sides (section 121). The bug above did not produce an EMPTY
# list, it produced a SHORT one, so "non-empty" would not have caught it.
# These floors are the shape of the truth, not its exact value: SOURCES only
# grows, and CLI_ONLY has been five since it was introduced.
n_sources=$(printf '%s\n' "$sources" | grep -c .)
n_cli=$(printf '%s\n' "$cli" | grep -c .)
[ "$n_sources" -ge 25 ] || { echo "core_check: read only $n_sources TU(s) from upstream SOURCES (floor 25) -- the extraction is broken, not the tree"; exit 2; }
[ "$n_cli" -eq 5 ] || { echo "core_check: read $n_cli TU(s) from CLI_ONLY, expected 5 -- upstream changed the CLI split, or the extraction is broken; confirm which before editing CORE"; exit 2; }
want=$(comm -23 <(printf '%s\n' "$sources") <(printf '%s\n' "$cli"))
missing=$(comm -13 <(printf '%s\n' "$core") <(printf '%s\n' "$want"))
extra=$(comm -23 <(printf '%s\n' "$core") <(printf '%s\n' "$want"))
if [ -z "$missing" ] && [ -z "$extra" ]; then
  echo "core_check: build.sh CORE matches upstream SOURCES minus CLI_ONLY ($(printf '%s\n' "$core" | wc -l) TUs)"; exit 0
fi
[ -n "$missing" ] && echo "core_check: upstream TU(s) MISSING from build.sh CORE (an AOT link fails the moment one is referenced): $(echo $missing)"
[ -n "$extra" ] && echo "core_check: build.sh CORE names TU(s) upstream no longer builds: $(echo $extra)"
exit 1
