#!/usr/bin/env bash
# ouroboros behavioral-parity oracle.
#
# For every sample program, compile+run it with the self-hosted compiler
# (ouroboros.eigs) and run the same source through the C eigenscript directly.
# The two stdouts must be byte-identical — proof that the EigenScript-emitted
# bytecode means the same thing on the real VM as the C compiler's output.
set -uo pipefail
cd "$(dirname "$0")/.."
EIGS="${EIGS:-./eigs}"
fail=0
n=0

echo "--- behavioral parity (self-hosted vs C evaluator) ---"
for prog in test/programs/*.eigs; do
  n=$((n+1))
  name="$(basename "$prog")"
  ref="$("$EIGS" "$prog" 2>/dev/null)"
  got="$("$EIGS" ouroboros.eigs "$prog" 2>/dev/null)"
  if [ "$ref" = "$got" ]; then
    echo "PASS: $name"
  else
    echo "FAIL: $name"
    echo "  C:         $(printf '%s' "$ref" | tr '\n' '|')"
    echo "  ouroboros: $(printf '%s' "$got" | tr '\n' '|')"
    fail=1
  fi
done

# Negative cases: the front-end must REJECT (not silently skip) input the C
# parser/lexer also rejects. (`+=`/bitwise `& | ^ ~ << >>` USED to be here, but
# they are now fully supported — see test/programs/bitwise_ops.eigs parity.)
# Both sides are checked: the C oracle must reject too, so a reject case can't
# silently rot into a valid program the front-end wrongly refuses.
echo "--- reject (front-end raises where the C parser/lexer raises) ---"
reject_one() {
  printf '%s\n' "$1" > /tmp/ouro_reject.eigs
  if "$EIGS" /tmp/ouro_reject.eigs >/dev/null 2>&1; then
    echo "FAIL: C oracle accepted [$(printf '%s' "$1" | tr '\n' ';')] (reject case is stale)"; fail=1
  elif "$EIGS" ouroboros.eigs /tmp/ouro_reject.eigs >/dev/null 2>&1; then
    echo "FAIL: accepted [$(printf '%s' "$1" | tr '\n' ';')] (should reject)"; fail=1
  else
    echo "PASS: rejected [$(printf '%s' "$1" | tr '\n' ';')]"
  fi
  rm -f /tmp/ouro_reject.eigs
}
reject_one 'print of (3 @ 4)'
reject_one 'print of (3 ` 4)'
reject_one 'x is $5'
# Dot-postfix on num/str/list literals: the C parser rejects these at parse
# time (only [idx] postfix is allowed there); the front-end used to accept
# them and fail at runtime -- a silent parse-acceptance divergence (#57).
reject_one 'z is [10,20].x'
reject_one 'z is "ab".foo'
reject_one 'z is 5 .foo'
# Statement terminator (#326, v0.23.0 pin): leftover tokens after a simple
# statement are a parse error — "one statement per line".
reject_one 'x is 2 x is 3'
# v0.24.0: upstream #351 closed the dot-/index-assign terminator gap — these
# were the stmt_terminator_gap.eigs matched-bug canary until the pin moved.
reject_one 'd is {"k": 1}
d.k is 2 3'
reject_one 'l is [1]
l[0] is 8 9'
reject_one 'l is [1]
l[0] += 1 4'

echo "--- bootstrap (full self-host: front-end + codegen, byte-exact fixed point) ---"
if "$EIGS" test/bootstrap.eigs 2>/dev/null | grep -q "PASS"; then
  echo "PASS: bootstrap fixed point"
else
  echo "FAIL: bootstrap"; fail=1
fi

echo "---"
if [ "$fail" -eq 0 ]; then echo "ALL PASSED ($n programs + bootstrap)"; else echo "SOME FAILED"; fi
exit "$fail"
