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

# Negative cases: the front-end must REJECT (not silently skip) characters the C
# lexer rejects — the `=` of `+=`, bitwise `& | ^ ~`, etc. Silently skipping them
# produced a silently-WRONG parse (`x += 3` → `x + 3` discarded; `~5` → `5`), so
# these can't be parity cases (the C VM compiles `+=`/bitwise) — ouroboros must
# error (non-zero exit) instead.
echo "--- reject (front-end raises on unknown characters, not silent-skip) ---"
reject_one() {
  printf '%s\n' "$1" > /tmp/ouro_reject.eigs
  if "$EIGS" ouroboros.eigs /tmp/ouro_reject.eigs >/dev/null 2>&1; then
    echo "FAIL: accepted [$(printf '%s' "$1" | tr '\n' ';')] (should reject)"; fail=1
  else
    echo "PASS: rejected [$(printf '%s' "$1" | tr '\n' ';')]"
  fi
  rm -f /tmp/ouro_reject.eigs
}
reject_one 'print of (~5)'
reject_one 'print of (6 & 3)'
reject_one 'x is 5
x += 3
print of x'

echo "--- bootstrap (full self-host: front-end + codegen, byte-exact fixed point) ---"
if "$EIGS" test/bootstrap.eigs 2>/dev/null | grep -q "PASS"; then
  echo "PASS: bootstrap fixed point"
else
  echo "FAIL: bootstrap"; fail=1
fi

echo "---"
if [ "$fail" -eq 0 ]; then echo "ALL PASSED ($n programs + bootstrap)"; else echo "SOME FAILED"; fi
exit "$fail"
