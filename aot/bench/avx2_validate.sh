#!/usr/bin/env bash
# Validate the output-axis SIMD matmul (and the reassociated reductions) on the
# host's widest SIMD — AVX2 (4-wide) in CI, vs the SSE2 (2-wide) dev box:
#   (1) correctness at width 4 — the AOT differential harness (byte-exact, plus
#       the *_tol reductions within tolerance) has only ever run at width 2;
#   (2) AOT_VW is actually 4 under -march=native;
#   (3) the width speedup: AVX2(4) vs SSE2(2) on the SAME machine and program.
# Run with EIGS_DIR / EIGS pointing at an EigenScript checkout.
set -uo pipefail
cd "$(dirname "$0")/.."   # -> aot/
EIG="${EIGS:-../../EigenScript/src/eigenscript}"
SRC="${EIGS_DIR:-../../EigenScript}/src"
DEFS='-DEIGENSCRIPT_EXT_HTTP=0 -DEIGENSCRIPT_EXT_MODEL=0 -DEIGENSCRIPT_EXT_DB=0 -DEIGENSCRIPT_VERSION="aot"'

echo "== host =="
grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ //'
if grep -qw avx2 /proc/cpuinfo; then echo "AVX2: present"; else echo "AVX2: ABSENT"; fi
echo

echo "== correctness at native SIMD width (AOT differential harness vs the VM) =="
EIGS="$EIG" bash test/run.sh 2>&1 | grep -vE '^\[load' | grep -E 'FAIL|passed' | tail -2
echo

probe_vw() {  # $1 = arch flags -> prints AOT_VW
  printf '#include "aot_rt.h"\n#include <stdio.h>\nint main(void){printf("%%d\\n",AOT_VW);return 0;}\n' > /tmp/vwp.c
  eval gcc -O2 "$1" $DEFS -I. -I"$SRC" /tmp/vwp.c build/libeigsrt.a -lm -lpthread -o /tmp/vwp 2>/dev/null && /tmp/vwp
}

PROG=bench/matmul.eigs
ref="$("$EIG" "$PROG")"
echo "== matmul SIMD-width A/B ($PROG, d_model=512, 100 passes) =="

AOT_ARCH='-msse2 -mno-avx -mno-avx2' bash build.sh "$PROG" /tmp/mm_sse2 >/dev/null 2>&1
vw2=$(probe_vw '-msse2 -mno-avx -mno-avx2')
g2="$(/tmp/mm_sse2)"; [ "$ref" = "$g2" ] && p2=byte-exact || p2=DIFF
t2=$( { /usr/bin/time -f %e /tmp/mm_sse2 >/dev/null; } 2>&1 )
echo "SSE2 (AOT_VW=$vw2): ${t2}s  parity=$p2"

AOT_ARCH='-march=native' bash build.sh "$PROG" /tmp/mm_native >/dev/null 2>&1
vwN=$(probe_vw '-march=native')
gN="$(/tmp/mm_native)"; [ "$ref" = "$gN" ] && pN=byte-exact || pN=DIFF
tN=$( { /usr/bin/time -f %e /tmp/mm_native >/dev/null; } 2>&1 )
echo "native (AOT_VW=$vwN): ${tN}s  parity=$pN"

awk -v a="$t2" -v b="$tN" 'BEGIN{ if(a+0>0 && b+0>0) printf "\nwidth speedup native vs SSE2: %.2fx (AOT_VW %s -> %s)\n", a/b, "'"$vw2"'", "'"$vwN"'" }'
