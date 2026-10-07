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

# #1637 (EigenScript#1647): raw number reads. Since the bool type a Value's
# number member (data.num_) and a slot's (d_) mean something only when the
# type says number. The guarantee is STRUCTURAL: aot_rt.h's reader block is
# the only code that names VAL_NUM_RAW / SLOT_NUM_RAW, and it ends by
# redefining both macros and renaming both members out of reach, so a raw
# read after it -- the rest of aot_rt.h, every program compile.eigs emits
# (however its text was built), the test C that includes the header -- does
# not compile. This checks what the compiler cannot:
#   1. the block exists once and keeps its six poison lines;
#   2. each raw read inside it is a DECLARED reader, one line, whose type
#      test comes before the member (examined == declared > 0);
#   3. no raw macro, member or bare builtin call (`->data.builtin(`, which
#      must go through AOT_GATED) is named anywhere else: every .c/.h under
#      aot/ and compile.eigs, derived by find, except the dated provenance
#      dirs aot/bench/*-20??????/ (frozen records of a measurement).
# Behavioural witness of 2: aot/test/t399_bool_raw_read.eigs.
RT="$HERE/aot_rt.h"
READERS="aot_num_if aot_num_or aot_num_proven aot_num_put aot_num_init aot_slot_num_if aot_slot_num_proven"
b0=$(grep -n 'THE raw number readers' "$RT" | cut -d: -f1); b1=$(grep -n 'end of the raw number readers' "$RT" | cut -d: -f1)
if [ "$(printf '%s\n' "$b0" | grep -c .)" -ne 1 ] || [ "$(printf '%s\n' "$b1" | grep -c .)" -ne 1 ] || [ "$b0" -ge "$b1" ]; then
    echo "core_check: aot_rt.h's raw-number reader block is missing or duplicated (begin='$b0' end='$b1')"; fail_n=1; b0=0; b1=0
fi
block=$(sed -n "${b0},${b1}p" "$RT")
n_poison=$(printf '%s\n' "$block" | grep -cE '^#(undef (VAL|SLOT)_NUM_RAW|define (VAL_NUM_RAW\(v\)|SLOT_NUM_RAW\(s\)|num_|d_)[[:space:]]+aot_raw_[a-z_]+)[[:space:]]*$')
[ "$n_poison" -eq 6 ] || { echo "core_check: reader block has $n_poison of its 6 poison lines (#undef/#define of the two macros, the two members)"; fail_n=1; }
seen=""; n_rd=0
while IFS= read -r l; do
    nm=$(printf '%s\n' "$l" | sed -nE 's/^static inline [a-z]+ (aot_[a-z_]+)\(.*/\1/p')
    pre=${l%%_NUM_RAW(*}
    case " $READERS " in *" $nm "*) ;; *) echo "core_check: raw read in the reader block outside a declared reader: $l"; fail_n=1; continue ;; esac
    case "$pre" in
        *'{ if (v && v->type == VAL_NUM) '*|*'{ if (slot_is_num(s)) '*|*'{ v->type = VAL_NUM; '*) ;;
        *) echo "core_check: reader $nm reads the member before testing the type: $l"; fail_n=1 ;;
    esac
    seen="$seen $nm"; n_rd=$((n_rd+1))
done < <(printf '%s\n' "$block" | grep -E '(VAL|SLOT)_NUM_RAW\(' | grep -vE '^#')
n_decl=$(echo $READERS | wc -w)
[ "$n_rd" -eq "$n_decl" ] && [ "$(echo $seen | tr ' ' '\n' | sort -u | wc -l)" -eq "$n_decl" ] ||
    { echo "core_check: reader block has $n_rd raw-read line(s) [${seen# }], declared $n_decl [$READERS]"; fail_n=1; }
nr_files=0; nr_bad=0; nr_prov=0
while IFS= read -r f; do
    case "$f" in "$HERE"/bench/*-20[0-9][0-9][0-9][0-9][0-9][0-9]/*) nr_prov=$((nr_prov+1)); continue ;; esac
    nr_files=$((nr_files+1))
    bad=$(grep -nE '(VAL|SLOT)_NUM_RAW|data\.num|(\.|->)d_?([^A-Za-z0-9_(]|$)|->data\.builtin[[:space:]]*\(' "$f")
    [ "$f" = "$RT" ] && bad=$(printf '%s\n' "$bad" | awk -F: -v a="$b0" -v b="$b1" 'NF && ($1 < a || $1 > b)')
    if [ -n "$bad" ]; then
        printf '%s\n' "$bad" | sed "s#^#core_check: raw number/builtin access outside the reader block in ${f#$HERE/}:#"
        nr_bad=$((nr_bad + $(printf '%s\n' "$bad" | grep -c .)))
    fi
done < <(find "$HERE" -path "$HERE/build" -prune -o \( -name '*.c' -o -name '*.h' -o -name compile.eigs \) -type f -print | sort)
[ "$nr_files" -ge 3 ] || { echo "core_check: raw-read scan examined $nr_files file(s) -- expected aot_rt.h, compile.eigs and the test C at least; the scan is broken, not the tree"; fail_n=1; }
[ "$nr_bad" -eq 0 ] || fail_n=1
echo "core_check: raw-read scan: readers=$n_rd/$n_decl poison=$n_poison/6 files=$nr_files violations=$nr_bad provenance_skipped=$nr_prov"

if [ "$fail_n" = 0 ]; then
    echo "core_check: build.sh derives CORE from $EIGS_DIR ($n_core TUs = SOURCES $n_src - CLI_ONLY $n_cli), no literal list"
    exit 0
fi
exit 1
