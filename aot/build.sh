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
DEFS="-DEIGENSCRIPT_EXT_HTTP=0 -DEIGENSCRIPT_EXT_MODEL=0 -DEIGENSCRIPT_EXT_DB=0 -DEIGENSCRIPT_VERSION=\"aot\""
CFLAGS="-O3 -march=native"   # -march=native so the packed guard hits the host's widest SIMD
LIB="build/libeigsrt.a"
CORE="eigenscript lexer parser builtins builtins_tensor hash arena state strbuf \
      ext_store fmt lint chunk compiler vm jit trace eigs_embed"

# (Re)build the runtime static lib if missing or any runtime .c is newer.
mkdir -p build
if [ ! -f "$LIB" ] || [ -n "$(find "$SRC" -name '*.c' -newer "$LIB" 2>/dev/null)" ]; then
    objs=""
    for f in $CORE; do
        eval gcc $CFLAGS $DEFS -I"$SRC" -c "$SRC/$f.c" -o "build/$f.o"
        objs="$objs build/$f.o"
    done
    ar rcs "$LIB" $objs
fi

# Transpile + link against the cached lib.
GEN="$(mktemp /tmp/aot_gen.XXXXXX.c)"
trap 'rm -f "$GEN"' EXIT
"$EIG" compile.eigs "$PROG" > "$GEN"
eval gcc $CFLAGS $DEFS -I. -I"$SRC" "$GEN" "$LIB" -lm -lpthread -o "$OUT"
