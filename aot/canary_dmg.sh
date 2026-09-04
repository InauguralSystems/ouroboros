#!/bin/bash
# DMG blast-radius canary for the AOT.
#
# Builds DMG with the working-tree compiler, runs a fixed prefix of
# cpu_instrs.gb, and diffs the output against a committed baseline. It is
# the widest real program the AOT compiles, so it catches emitter changes
# that every fixture misses.
#
# It FAILS when there is nothing to compare against. Bought round 99: a
# /tmp-resident baseline vanished, the canary silently captured a fresh
# one, printed "baseline captured", and the gate went green having
# compared zero bytes -- the vacuity failure mode in a check whose whole
# job is comparison. Baselines live in the repo now, and "no baseline"
# is an error with the exact command to mint one.
#
# The cycle budget is passed as `--cycles N`, which is what dmg.eigs
# parses. Round 99 first wrote it as a bare positional; dmg.eigs ignored
# it and every run silently used its own 50M default, so the knob read
# as live while moving nothing -- a second vacuity, in the dial rather
# than the comparison. 50M cycles is now the deliberate default (~7s).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
DMG="${DMG_DIR:-$HERE/../../DMG}"
BASE="$HERE/test/canary/dmg_cpu_instrs.out"
CYCLES="${CYCLES:-50000000}"
strip_timing() { grep -vE 'MHz|[0-9]ms|seconds|cycles/s|elapsed'; }

[ -f "$DMG/dmg.eigs" ] || { echo "canary: no DMG checkout at $DMG (set DMG_DIR)"; exit 2; }
out=$(mktemp); bin=$(mktemp -u)
bash "$HERE/build.sh" "$DMG/dmg.eigs" "$bin" >/dev/null 2>&1 || { echo "canary: DMG BUILD FAILED"; exit 1; }
( cd "$DMG" && timeout 900 "$bin" roms/cpu_instrs.gb --cycles "$CYCLES" ) >"$out" 2>&1
mhz=$(grep -oE '[0-9.]+ MHz' "$out" | head -1)

if [ ! -f "$BASE" ]; then
  echo "canary: NO BASELINE at $BASE -- refusing to self-baseline."
  echo "canary: mint one deliberately with:  cp $out $BASE"
  rm -f "$bin"; exit 1
fi
if diff <(strip_timing <"$BASE") <(strip_timing <"$out") >/dev/null; then
  echo "canary: byte-identical (${mhz:-?}, $CYCLES cycles)"
  rm -f "$out" "$bin"; exit 0
fi
echo "canary: DIVERGED from $BASE"
diff <(strip_timing <"$BASE") <(strip_timing <"$out") | head -30
echo "canary: full output kept at $out"
rm -f "$bin"; exit 1
