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

# The oracle compares two stdouts. If the interpreter is missing, both are
# EMPTY and every comparison passes — 57 PASS lines on a fresh clone, because
# `./eigs` is a gitignored dev symlink. A parity suite that proves self-hosting
# by diffing silence against silence is the one failure mode that discredits
# every other number here, so make a missing interpreter a hard stop rather
# than a green run. (EigenScript has the upstream twin of this guard: test
# [99d], the #681 binary-fingerprint check.)
if ! command -v "$EIGS" >/dev/null 2>&1 && ! [ -x "$EIGS" ]; then
    echo "FATAL: EIGS is not executable: $EIGS" >&2
    echo "  ./eigs is a gitignored dev symlink; on a fresh clone either create it" >&2
    echo "  (ln -s ../EigenScript/src/eigenscript eigs) or run: EIGS=eigenscript $0" >&2
    exit 2
fi

fail=0
n=0

# Parity contract (#101): the string compare alone is vacuous when the
# reference run itself dies — a program that errors on BOTH sides degrades to
# diffing pre-error stdout (or silence against silence) and stays green across
# pin bumps that break it. So the reference must exit 0 with nonempty stdout,
# and ouroboros must exit 0 too. Opt-out: an `_err.eigs` filename suffix
# declares the program errors at the current pin ON PURPOSE (same convention
# as aot/test/run.sh); there the contract inverts — both sides must DIE, and a
# reference that starts exiting 0 fails loudly so the exemption can't go stale.
echo "--- behavioral parity (self-hosted vs C evaluator) ---"
for prog in test/programs/*.eigs; do
  n=$((n+1))
  name="$(basename "$prog")"
  ref="$("$EIGS" "$prog" 2>/dev/null)"; ref_rc=$?
  got="$("$EIGS" ouroboros.eigs "$prog" 2>/dev/null)"; got_rc=$?
  case "$name" in
    *_err.eigs)
      if [ "$ref_rc" -eq 0 ]; then
        echo "FAIL: $name (_err program but the C oracle exits 0 — the opt-out is stale; drop the suffix)"; fail=1; continue
      fi
      if [ "$got_rc" -eq 0 ]; then
        echo "FAIL: $name (C oracle errors rc=$ref_rc, ouroboros exits 0 — runs past the error)"; fail=1; continue
      fi
      ;;
    *)
      if [ "$ref_rc" -ne 0 ] || [ -z "$ref" ]; then
        echo "FAIL: $name (vacuous reference: rc=$ref_rc, stdout ${#ref} bytes — a parity claim needs a clean, nonempty reference; rename to *_err.eigs if it errors on purpose)"; fail=1; continue
      fi
      if [ "$got_rc" -ne 0 ]; then
        echo "FAIL: $name (reference exits 0, ouroboros rc=$got_rc)"; fail=1; continue
      fi
      ;;
  esac
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
# v0.25.0 (upstream #378): hex is INTEGER-only and the prefix is decisive —
# hex-float forms and a bare 0x are loud parse errors on both sides.
reject_one 'x is 0x1p4'
reject_one 'x is 0x.8'
reject_one 'x is 0x'
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

# Inverse-direction rejects (#101): reject_one's population is parse/lex-time
# errors the FRONT-END must refuse. This tier is the complement — programs the
# C oracle rejects at RUN time, where a miscompile shows up as ouroboros
# quietly accepting (running past the error, the silent-wrong-answer class).
# Both directions are still asserted (a case the C oracle starts accepting is
# stale), plus pre-death stdout parity, so a partial run-past can't hide.
# #99's fixes (unterminated strings, orphan dedents, ...) will grow this list —
# only cases BOTH sides reject today belong here.
echo "--- must_reject (both sides must die at run time) ---"
must_reject() {
  printf '%s\n' "$1" > /tmp/ouro_must_reject.eigs
  c_out="$("$EIGS" /tmp/ouro_must_reject.eigs 2>/dev/null)"; c_rc=$?
  o_out="$("$EIGS" ouroboros.eigs /tmp/ouro_must_reject.eigs 2>/dev/null)"; o_rc=$?
  if [ "$c_rc" -eq 0 ]; then
    echo "FAIL: must_reject case is stale — C oracle ACCEPTS [$(printf '%s' "$1" | tr '\n' ';')]"; fail=1
  elif [ "$o_rc" -eq 0 ]; then
    echo "FAIL: must_reject: ouroboros ACCEPTS [$(printf '%s' "$1" | tr '\n' ';')] (C oracle rejects, rc=$c_rc)"; fail=1
  elif [ "$c_out" != "$o_out" ]; then
    echo "FAIL: must_reject: pre-death stdout diverges [$(printf '%s' "$1" | tr '\n' ';')]"
    echo "  C:         $(printf '%s' "$c_out" | tr '\n' '|')"
    echo "  ouroboros: $(printf '%s' "$o_out" | tr '\n' '|')"
    fail=1
  else
    echo "PASS: must_reject [$(printf '%s' "$1" | tr '\n' ';')]"
  fi
  rm -f /tmp/ouro_must_reject.eigs
}
must_reject 'print of undefined_var_xyz'
# v0.39.0 pin (upstream #898 / ouroboros #98): reading a field off null raises.
must_reject 'n is null
print of n.f'
# v0.39.0 pin: a global first bound in a top-level `for` body is loop-local —
# reading it afterwards is undefined-variable on BOTH sides (this was the
# second half of module_name_in_block.eigs until it degraded at a pin bump).
# Matched-behavior canary for upstream EigenScript#959 (intended vs
# regression, verdict pending): if a future pin accepts this again, the
# stale-case check fires — fold it back into the parity program in the same
# EIGS_REF bump.
must_reject 'for q in [1, 2]:
    acc2 is q
define show2() as:
    return acc2
print of (show2 of null)'
# #99 item 4: a string/f-string still open at end-of-source must die loudly on
# both sides — the front-end used to emit the buffer at EOF, silently
# SWALLOWING every following line (the print of 99 here vanished at rc=0).
must_reject 'x is "abc
print of 99'
must_reject 'x is f"abc {1}
print of 99'
must_reject 'x is f"abc {1 + 2'
# #99 item 5: a dedent matching no outer indent level is a lexer error
# (lexer.c "indentation does not match any outer level"); the front-end used
# to accept it and silently change block structure.
must_reject 'if 1:
        print of 1
    print of 2
print of 3'
# #99 item 6 (#634): `prev of` requires a variable name — a literal operand
# has no assignment history and used to print null at rc=0.
must_reject 'print of (prev of 5)'
# #99 item 8: exact-keyword expects — `for x of` is not `for x in`; the
# front-end accepted ANY keyword where the C parser expects TOK_IN.
must_reject 'for x of [1, 2]:
    print of x'

# Bootstrap: byte-exact fixed point AND the self-compiled program must RUN
# (#101). The grep-PASS alone was vacuous against the v0.33.0 operand-width
# class: garbage bytecode left the originals bound and the bytes trivially
# equal. Now bootstrap.eigs proves the rebinding executed (sentinels) and
# executes the self-compiled test program between markers; here we diff that
# block against the C evaluator running the same file directly.
echo "--- bootstrap (full self-host: front-end + codegen, byte-exact fixed point) ---"
boot_out="$("$EIGS" test/bootstrap.eigs 2>/dev/null)"; boot_rc=$?
prog_ref="$("$EIGS" test/bootstrap_prog.eigs 2>/dev/null)"; prog_rc=$?
prog_got="$(printf '%s\n' "$boot_out" \
  | sed -n '/^== bootstrap-prog-begin ==$/,/^== bootstrap-prog-end ==$/p' | sed '1d;$d')"
if [ "$boot_rc" -ne 0 ] || ! printf '%s\n' "$boot_out" | grep -q "PASS"; then
  echo "FAIL: bootstrap (rc=$boot_rc)"
  printf '%s\n' "$boot_out" | sed 's/^/  /'
  fail=1
elif [ "$prog_rc" -ne 0 ] || [ -z "$prog_ref" ]; then
  echo "FAIL: bootstrap reference is vacuous (test/bootstrap_prog.eigs: rc=$prog_rc, stdout ${#prog_ref} bytes)"; fail=1
elif [ "$prog_got" != "$prog_ref" ]; then
  echo "FAIL: bootstrap — self-compiled program output != C evaluator"
  echo "  C:         $(printf '%s' "$prog_ref" | tr '\n' '|')"
  echo "  self-host: $(printf '%s' "$prog_got" | tr '\n' '|')"
  fail=1
else
  echo "PASS: bootstrap fixed point (and the self-compiled program runs)"
fi

echo "---"
if [ "$fail" -eq 0 ]; then echo "ALL PASSED ($n programs + bootstrap)"; else echo "SOME FAILED"; fi
exit "$fail"
