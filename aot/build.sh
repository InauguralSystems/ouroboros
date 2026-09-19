#!/bin/bash
# AOT: compile an EigenScript program to a native binary.
#   build.sh program.eigs [out_binary]
# Transpiles via compile.eigs, then links the generated C against a cached
# static lib of the EigenScript runtime (upstream SOURCES minus CLI_ONLY --
# main, repl, step, tape_read, bundle: 23 TUs, the same set LSP_SOURCES and
# DAP_SOURCES take. This line used to say "minus main.c", which is 27, and
# it is the line a future reader patches CORE from -- the fourth drift of
# this list was pre-loaded in a comment. aot/core_check.sh is the authority
# and derives both sets from `make -pqRr`). The lib is rebuilt only when a runtime .c changes, so
# repeated builds are ~1s instead of recompiling the whole runtime each time.
# Runtime checkout is ../../EigenScript (override with EIGS_DIR=).
set -euo pipefail
cd "$(dirname "$0")"

EIGS_DIR="${EIGS_DIR:-../../EigenScript}"
EIG="${EIGS:-$EIGS_DIR/src/eigenscript}"
SRC="$EIGS_DIR/src"
PROG="$1"
OUT="${2:-./a.out}"
# [ -f ] IS the oracle's admission predicate -- bash's -f tests S_ISREG,
# which is exactly what the runtime's read_file_util requires (#314: any
# non-regular file is "cannot read file", rc 1). This check was deleted once
# as part of a "kludge" (the mktemp stand-in beside it WAS a kludge; the -f
# itself was the only precise regular-file test in the toolchain) and a
# review round then measured /dev/null compiling silently to the empty
# program. The drivers keep best-effort guards (file_exists + is_dir) for
# direct callers, but those cannot see the non-regular class -- read_text
# collapses a refused device file and an empty regular file to the same "" --
# until an is_file builtin exists upstream.
if [ ! -f "$PROG" ]; then
  echo "build.sh: not a regular source file: $PROG" >&2
  exit 1
fi
# EIGENSCRIPT_VERSION must survive the eval'd gcc line as a C string: the
# escaped inner quotes get eaten by eval, leaving a bare identifier — harmless
# while only stringified, a build break once code compares it (trace.c #411).
DEFS="-DEIGENSCRIPT_EXT_HTTP=0 -DEIGENSCRIPT_EXT_MODEL=0 -DEIGENSCRIPT_EXT_DB=0 -DEIGENSCRIPT_VERSION='\"aot\"'"
# -ffp-contract=off: the VM never fuses multiply-add, so neither may we. Without
# it, the num_guard-elided matmul (`vadd(acc, vmul(x,w))`, no guard barrier
# between them) gets fused into a single-rounding FMA on FMA-capable targets
# (AVX2 -march=native), diverging from the VM's two-rounding mul-then-add. The
# guarded path's vguard already blocks fusion; this makes the elided path match.
# (round 171) Fixed code alignment. The DMG canary swung 3% in CYCLES at an
# identical instruction count (3.087G) when seven COLD lines changed in a
# 20k-line emission: the hot dispatch loop's placement moved. Measured on the
# dev box (Goldmont, no uop cache): default alignment 1586M-1637M cycles
# across five layouts of the same hot code; with these flags 1597M-1609M.
# The flags cost ~1% against the luckiest default layout and make the canary
# a measurement of the emitted code instead of where cold code landed.
# Rank rounds by host instructions per emulated cycle regardless.
ALIGN="-falign-functions=64 -falign-jumps=32 -falign-loops=32"
CFLAGS="-O3 -ffp-contract=off $ALIGN ${AOT_ARCH:--march=native}"   # widest host SIMD; AOT_ARCH overrides (e.g. -msse2 to force 2-wide)
BDIR="build"
# (round 146, #136) AOT_SAN=asan: an AddressSanitizer/LeakSanitizer build of
# the runtime library AND the program, in its own object dir so it never
# shares a stamp with the release lib. test/leak.sh uses it; nothing else.
if [ "${AOT_SAN:-}" = "asan" ]; then
    CFLAGS="-O1 -g -fno-omit-frame-pointer -fsanitize=address -ffp-contract=off ${AOT_ARCH:--march=native}"
    BDIR="build/asan"
fi
LIB="$BDIR/libeigsrt.a"
# CORE IS DERIVED FROM THE RUNTIME WE ARE ACTUALLY LINKING AGAINST, never
# hand-listed (ouroboros#232).
#
# It was a literal list and it drifted FOUR times: ext_http.c after a VM
# refactor; builtins_host.c when upstream #741/#812 split the host-only
# builtins out at v0.35.0; builtins_buf/fsutil/task after v0.43.0 (the
# symptom being `undefined reference to read_file_util` when linking DMG,
# i.e. the AOT could not build the widest real program it has); and a fourth
# pre-loaded in this very comment, which used to say the set was "SOURCES
# minus main.c" -- 27 TUs -- while the list below it was 23.
#
# The repeated advice was "re-diff this list at every EIGS_REF bump". That
# is advice, it failed every time, and the mechanical answer is to have no
# list: SOURCES minus CLI_ONLY, read from $EIGS_DIR/Makefile at build time.
#
# This also fixes a subtler error. A hand list can only be right for ONE
# runtime, and there are two in play: CI links the PIN (v0.43.0, no
# builtins_buf/fsutil/task) while a developer's sibling checkout is main
# (which has them). Deriving is correct for both, and a pin bump needs no
# edit here at all.
#
# `make -pqRr` gives the post-expansion database, so continuations, variable
# indirection and += are all handled. No fallback parse: if the authority
# cannot be read, refuse to build rather than link a guessed set.
# `make -q` EXITS 1 when a target is out of date, which is its normal answer
# and not an error -- under this file's `set -euo pipefail` that silently
# killed the build with an empty log. Capture first, then parse, and use awk
# rather than `head -1` so no stage can take a SIGPIPE either.
mk_tus() {  # $1 = Makefile variable -> TU basenames, one per line
    local db line
    db=$(make -C "$EIGS_DIR" -pqRr 2>/dev/null || true)
    line=$(printf '%s\n' "$db" | awk -v v="$1" '$1==v && ($2==":=" || $2=="=") {print; exit}')
    printf '%s\n' "$line" | tr ' ' '\n' |
        sed -nE 's#^(\$\(SRC_DIR\)|[a-z_0-9]+)/([a-z_0-9]+)\.c$#\2#p' | sort -u
}
CORE_SOURCES="$(mk_tus SOURCES)"
CORE_CLI="$(mk_tus CLI_ONLY)"
[ "$(printf '%s\n' "$CORE_SOURCES" | grep -c .)" -ge 20 ] ||
    { echo "build.sh: read $(printf '%s\n' "$CORE_SOURCES" | grep -c .) TU(s) from $EIGS_DIR/Makefile SOURCES -- cannot derive CORE, refusing to link a guess" >&2; exit 1; }
[ "$(printf '%s\n' "$CORE_CLI" | grep -c .)" -eq 5 ] ||
    { echo "build.sh: CLI_ONLY has $(printf '%s\n' "$CORE_CLI" | grep -c .) TU(s), expected 5 -- upstream changed the CLI split; confirm before linking" >&2; exit 1; }
CORE="$(comm -23 <(printf '%s\n' "$CORE_SOURCES") <(printf '%s\n' "$CORE_CLI") | tr '\n' ' ')"

# (Re)build the runtime static lib if missing, or if the runtime it was built
# FROM has changed at all.
#
# "Any .c newer than the lib" alone is not sufficient, and the gap is silent
# and expensive (ouroboros#96): building once with EIGS_DIR=<old-tag worktree>
# leaves a lib from that tag, and switching EIGS_DIR back to a checkout whose
# sources are OLDER than the lib satisfies the newer-check trivially, so every
# subsequent binary links the wrong runtime while the harness diffs it against
# the current VM. That mismatch shows up as a scatter of unrelated semantic
# failures — four observer tests, in the case that caught this — pointing
# nowhere near the actual cause.
#
# The stamp is identity, not recency: the resolved source path, the effective
# compile flags/defines/object list, and a content hash over every runtime .c
# AND .h. The first version kept `ls -l` size+name only and its `-newer`
# fallback scanned *.c — a same-size HEADER edit rebuilt nothing (#101), the
# exact stale-lib scatter described above; and a CFLAGS/AOT_ARCH/DEFS change
# linked objects built under the old flags. Content hashing also drops the
# recency fallback for good: a bare touch is not a new identity.
mkdir -p "$BDIR"
STAMP="$BDIR/.libsrc"
SHA="sha256sum"; command -v sha256sum >/dev/null 2>&1 || SHA="shasum -a 256"
SIG="$(cd "$SRC" && printf '%s\n%s\n' "$(pwd -P)" "$CFLAGS $DEFS $CORE" && $SHA *.c *.h 2>/dev/null)"
if [ ! -f "$LIB" ] || [ "$SIG" != "$(cat "$STAMP" 2>/dev/null)" ]; then
    objs=""
    for f in $CORE; do
        eval gcc $CFLAGS $DEFS -I"$SRC" -c "$SRC/$f.c" -o "$BDIR/$f.o"
        objs="$objs $BDIR/$f.o"
    done
    ar rcs "$LIB" $objs
    printf '%s' "$SIG" > "$STAMP"
fi

# Transpile + link against the cached lib.
#
# PDEFS (#86, F-OURO-34): bake the two resolution anchors main.c would have
# set for `eigenscript $PROG` — the program's own dir (g_script_dir) and the
# linked runtime's src dir (g_exe_dir; its ../lib is the stdlib). Absolutized,
# so the binary resolves the same files from any cwd the VM would from that
# cwd. Per-program by construction, so these defines must NOT join DEFS: the
# lib stamp hashes DEFS and a per-program define there would rebuild the
# cached runtime lib on every target change. aot_rt.h is header-only into
# GEN, so the define reaches the one TU that reads it.
GEN="$(mktemp /tmp/aot_gen.XXXXXX.c)"
trap 'rm -f "$GEN"' EXIT
PROG_DIR="$(cd "$(dirname "$PROG")" && pwd -P)"
SRC_ABS="$(cd "$SRC" && pwd -P)"
PDEFS="-DAOT_SCRIPT_DIR='\"$PROG_DIR\"' -DAOT_EXE_DIR='\"$SRC_ABS\"'"
# argv[2] is the stdlib root, used by compile.eigs to resolve `import` of a
# stdlib module (#121). Project-local modules resolve beside the program and
# do not need it.
# (ouroboros#147) The transpiler's resolution of literal load_file/import
# targets is a property of the SOURCE FILE, never of the cwd it happens to run
# in -- the same rule the VM follows since EigenScript#1056. Normally it runs
# from aot/ (the cd above). AOT_TRANSPILE_CWD runs it from another directory,
# with every path absolutized, so test/load_file_shadow.sh can drive it from
# the SHADOWING cwd of the #147 layout and prove the output does not move.
if [ -n "${AOT_TRANSPILE_CWD:-}" ]; then
  case "$EIG" in */*) EIG_ABS="$(cd "$(dirname "$EIG")" && pwd -P)/$(basename "$EIG")";; *) EIG_ABS="$EIG";; esac
  PROG_ABS="$PROG_DIR/$(basename "$PROG")"
  EIGS_DIR_ABS="$(cd "$EIGS_DIR" && pwd -P)"
  COMPILE_ABS="$(pwd -P)/compile.eigs"
  ( cd "$AOT_TRANSPILE_CWD" && "$EIG_ABS" "$COMPILE_ABS" "$PROG_ABS" "$EIGS_DIR_ABS" ) > "$GEN"
else
  "$EIG" compile.eigs "$PROG" "$EIGS_DIR" > "$GEN"
fi
eval gcc $CFLAGS $DEFS $PDEFS -I. -I"$SRC" "$GEN" "$LIB" -lm -lpthread -o "$OUT"
