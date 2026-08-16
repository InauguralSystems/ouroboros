#!/bin/bash
# AOT: compile an EigenScript program to a native binary.
#   build.sh program.eigs [out_binary]
# Transpiles via compile.eigs, then links the generated C against a cached
# static lib of the EigenScript runtime (SOURCES minus main.c — same set
# embed-smoke/lsp use). The lib is rebuilt only when a runtime .c changes, so
# repeated builds are ~1s instead of recompiling the whole runtime each time.
# Runtime checkout is ../../EigenScript (override with EIGS_DIR=).
set -euo pipefail
cd "$(dirname "$0")"

EIGS_DIR="${EIGS_DIR:-../../EigenScript}"
EIG="${EIGS:-$EIGS_DIR/src/eigenscript}"
SRC="$EIGS_DIR/src"
PROG="$1"
OUT="${2:-./a.out}"
# EIGENSCRIPT_VERSION must survive the eval'd gcc line as a C string: the
# escaped inner quotes get eaten by eval, leaving a bare identifier — harmless
# while only stringified, a build break once code compares it (trace.c #411).
DEFS="-DEIGENSCRIPT_EXT_HTTP=0 -DEIGENSCRIPT_EXT_MODEL=0 -DEIGENSCRIPT_EXT_DB=0 -DEIGENSCRIPT_VERSION='\"aot\"'"
# -ffp-contract=off: the VM never fuses multiply-add, so neither may we. Without
# it, the num_guard-elided matmul (`vadd(acc, vmul(x,w))`, no guard barrier
# between them) gets fused into a single-rounding FMA on FMA-capable targets
# (AVX2 -march=native), diverging from the VM's two-rounding mul-then-add. The
# guarded path's vguard already blocks fusion; this makes the elided path match.
CFLAGS="-O3 -ffp-contract=off ${AOT_ARCH:--march=native}"   # widest host SIMD; AOT_ARCH overrides (e.g. -msse2 to force 2-wide)
LIB="build/libeigsrt.a"
# CORE must stay exactly upstream's `SOURCES` minus `CLI_ONLY` (Makefile). It
# has now drifted twice (ext_http.c after a VM refactor; builtins_host.c when
# upstream #741/#812 split the host-only builtins — including read_file_util,
# which eigs_embed.c calls — into their own TU at v0.35.0). The symptom is a
# link error at pin-bump time, which is loud but burns a sweep: re-diff this
# list against the Makefile at every EIGS_REF bump.
CORE="eigenscript lexer parser builtins builtins_host builtins_tensor hash arena state strbuf \
      ext_store fmt lint chunk compiler vm jit trace eigs_embed"

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
mkdir -p build
STAMP="build/.libsrc"
SHA="sha256sum"; command -v sha256sum >/dev/null 2>&1 || SHA="shasum -a 256"
SIG="$(cd "$SRC" && printf '%s\n%s\n' "$(pwd -P)" "$CFLAGS $DEFS $CORE" && $SHA *.c *.h 2>/dev/null)"
if [ ! -f "$LIB" ] || [ "$SIG" != "$(cat "$STAMP" 2>/dev/null)" ]; then
    objs=""
    for f in $CORE; do
        eval gcc $CFLAGS $DEFS -I"$SRC" -c "$SRC/$f.c" -o "build/$f.o"
        objs="$objs build/$f.o"
    done
    ar rcs "$LIB" $objs
    printf '%s' "$SIG" > "$STAMP"
fi

# Transpile + link against the cached lib.
GEN="$(mktemp /tmp/aot_gen.XXXXXX.c)"
trap 'rm -f "$GEN"' EXIT
"$EIG" compile.eigs "$PROG" > "$GEN"
eval gcc $CFLAGS $DEFS -I. -I"$SRC" "$GEN" "$LIB" -lm -lpthread -o "$OUT"
