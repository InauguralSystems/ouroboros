#!/bin/bash
set -euo pipefail
gcc -O3 -ffp-contract=off -falign-functions=64 -falign-jumps=32 -falign-loops=32 -march=native \
  -DEIGENSCRIPT_EXT_HTTP=0 -DEIGENSCRIPT_EXT_MODEL=0 -DEIGENSCRIPT_EXT_DB=0 '-DEIGENSCRIPT_VERSION="aot"' \
  "-DAOT_SCRIPT_DIR=\"$3\"" '-DAOT_EXE_DIR="/home/jon/src/wt/es-v043/src"' \
  -I/home/jon/src/wt/ouro-ems-20260910/aot -I/home/jon/src/wt/es-v043/src \
  "$1" /home/jon/src/wt/ouro-ems-20260910/aot/build/libeigsrt.a -lm -lpthread -o "$2"
