#!/bin/bash
# Portable equivalent of provenance/compile_c.recorded.sh; no runtime build.
# Usage: EIGS_DIR=/absolute/runtime AOT_REPO=/absolute/compiler bash compile_c.sh SOURCE.c OUTPUT SCRIPT_DIR
set -euo pipefail
: "${EIGS_DIR:?set EIGS_DIR to the pinned runtime checkout}"
AOT_REPO="${AOT_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
if [ "$#" -ne 3 ]; then
    echo 'usage: compile_c.sh SOURCE.c OUTPUT SCRIPT_DIR' >&2
    exit 2
fi
"${CC:-gcc}" -O3 -ffp-contract=off -falign-functions=64 -falign-jumps=32 -falign-loops=32 -march=native \
    -DEIGENSCRIPT_EXT_HTTP=0 -DEIGENSCRIPT_EXT_MODEL=0 -DEIGENSCRIPT_EXT_DB=0 '-DEIGENSCRIPT_VERSION="aot"' \
    "-DAOT_SCRIPT_DIR=\"$3\"" "-DAOT_EXE_DIR=\"$EIGS_DIR/src\"" \
    -I"$AOT_REPO/aot" -I"$EIGS_DIR/src" \
    "$1" "$AOT_REPO/aot/build/libeigsrt.a" -lm -lpthread -o "$2"
