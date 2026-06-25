#!/bin/bash
# AOT slice 1: compile an EigenScript program to a native binary.
#   build.sh program.eigs [out_binary]
# Transpiles via compile.eigs, then links the generated C against the
# EigenScript runtime (the SOURCES list minus main.c — same set embed-smoke/lsp
# use). The runtime checkout is ../../EigenScript (override with EIGS_DIR=).
set -euo pipefail
cd "$(dirname "$0")"

EIGS_DIR="${EIGS_DIR:-../../EigenScript}"
EIG="${EIGS:-$EIGS_DIR/src/eigenscript}"
SRC="$EIGS_DIR/src"
PROG="$1"
OUT="${2:-./a.out}"
GEN="$(mktemp /tmp/aot_gen.XXXXXX.c)"
trap 'rm -f "$GEN"' EXIT

# 1. transpile EigenScript -> C
"$EIG" compile.eigs "$PROG" > "$GEN"

# 2. compile + link against runtime (minus main.c)
CORE="eigenscript lexer parser builtins builtins_tensor hash arena state strbuf \
      ext_store fmt lint chunk compiler vm jit trace eigs_embed"
SRCS=""
for f in $CORE; do SRCS="$SRCS $SRC/$f.c"; done

gcc -O2 -I. -I"$SRC" \
    -DEIGENSCRIPT_EXT_HTTP=0 -DEIGENSCRIPT_EXT_MODEL=0 -DEIGENSCRIPT_EXT_DB=0 \
    -DEIGENSCRIPT_VERSION='"aot"' \
    "$GEN" $SRCS -lm -lpthread -o "$OUT"
