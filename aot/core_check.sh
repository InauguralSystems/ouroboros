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
names() { tr ' \\' '\n\n' | sed -nE 's#^\$\(SRC_DIR\)/([a-z_0-9]+)\.c$#\1#p' | sort -u; }
core=$(sed -n '/^CORE="/,/"/p' "$HERE/build.sh" | tr -d '"\\' | sed 's/^CORE=//' | tr ' ' '\n' | grep -E '^[a-z_0-9]+$' | sort -u)
sources=$(grep -E '^SOURCES *:?=' "$MK" | names)
cli=$(grep -E '^CLI_ONLY *:?=' "$MK" | names)
want=$(comm -23 <(printf '%s\n' "$sources") <(printf '%s\n' "$cli"))
missing=$(comm -13 <(printf '%s\n' "$core") <(printf '%s\n' "$want"))
extra=$(comm -23 <(printf '%s\n' "$core") <(printf '%s\n' "$want"))
if [ -z "$missing" ] && [ -z "$extra" ]; then
  echo "core_check: build.sh CORE matches upstream SOURCES minus CLI_ONLY ($(printf '%s\n' "$core" | wc -l) TUs)"; exit 0
fi
[ -n "$missing" ] && echo "core_check: upstream TU(s) MISSING from build.sh CORE (an AOT link fails the moment one is referenced): $(echo $missing)"
[ -n "$extra" ] && echo "core_check: build.sh CORE names TU(s) upstream no longer builds: $(echo $extra)"
exit 1
