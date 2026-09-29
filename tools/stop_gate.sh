#!/bin/bash
# Stop-hook gate: Claude Code may not end a turn with UNCOMMITTED changes that
# break the self-host or the AOT transpiler. Clean tree (or changes outside the
# gated paths) exits 0 at once, so conversational stops cost nothing.
#
#  - ouroboros.eigs / src/ / test/ dirty  -> the whole self-host suite
#    (test/run.sh: 63 programs + rejects + bootstrap fixed point, ~13 s).
#  - aot/compile.eigs / aot/aot_rt.h / src/frontend.eigs dirty -> transpile a
#    sample of aot/test programs with the working tree AND with HEAD, and
#    syntax-check the C wherever the output changed.
#
# Both halves are judged AGAINST HEAD, never against a fixed expectation: a
# failure that HEAD shares (pin drift, an upstream break) is reported, not
# blocked. Only a regression this change introduced blocks the stop (exit 2).
# The AOT half never runs aot/build.sh: its build dir is shared and not safe
# to run twice (CLAUDE.md), and a stop gate must not race a builder's lib.
# Behavioural AOT parity stays with aot/test/run.sh; this gate does not claim it.
#
# Escape hatch if the gate itself misbehaves: touch /tmp/ouro_stop_gate_off.
set -u
[ -f /tmp/ouro_stop_gate_off ] && exit 0
cd "$(dirname "$0")/.." || exit 0
ROOT=$(pwd -P)

dirty() { [ -n "$(git status --porcelain -- "$@" 2>/dev/null)" ]; }
SELFHOST=0; AOT=0
dirty ouroboros.eigs src test && SELFHOST=1
dirty aot/compile.eigs aot/aot_rt.h src/frontend.eigs && AOT=1
[ $SELFHOST -eq 0 ] && [ $AOT -eq 0 ] && exit 0

# One deadline for the whole gate, inside the hook's 240 s: a hook killed by
# its timeout does not block the stop, so running out of time must be a loud
# block, never a silent pass (a hanging transpiler is exactly the red to catch).
DEADLINE=$((SECONDS + ${OURO_GATE_BUDGET_S:-220}))
left() { echo $((DEADLINE - SECONDS)); }
EIG=${EIGS:-}
[ -z "$EIG" ] && [ -x ./eigs ] && EIG=./eigs
EIG=$(command -v "${EIG:-eigenscript}" 2>/dev/null)   # a bare name (EIGS=eigenscript) resolves on PATH
if [ -z "$EIG" ] || [ ! -x "$EIG" ]; then
  echo "STOP GATE (ouroboros): no EigenScript VM found (EIGS, ./eigs, or eigenscript on PATH) — cannot check the dirty tree." >&2
  exit 2
fi
EIG=$(readlink -f "$EIG")
# An exported EIGS_DIR wins (the devcontainer sets one; its binary lives in /usr/local/bin).
EIGS_DIR=${EIGS_DIR:-$(cd "$(dirname "$EIG")/.." && pwd -P)}

W=$(mktemp -d "${TMPDIR:-/tmp}/ouro-stopgate.XXXXXX") || exit 0
trap 'rm -rf "$W"' EXIT
git archive HEAD | tar -x -C "$W" 2>/dev/null || { echo "STOP GATE (ouroboros): cannot extract HEAD for the baseline." >&2; exit 2; }
fail=0

if [ $SELFHOST -eq 1 ]; then
  # The suite runs ~13 s clean; a broken codegen usually HANGS programs, and
  # run.sh gives each hang 120 s (a planted fault took 319 s). Cap the whole
  # suite at ~4.6x its clean time: a timeout is a regression if HEAD runs clean.
  EIGS=$EIG timeout "${OURO_GATE_SUITE_S:-60}" bash test/run.sh > "$W/new.log" 2>&1; new_rc=$?
  if [ $new_rc -ne 0 ]; then
    ( cd "$W" && EIGS=$EIG timeout "${OURO_GATE_SUITE_S:-60}" bash test/run.sh ) > "$W/head.log" 2>&1; head_rc=$?
    grep -E '^FAIL' "$W/new.log" | sort -u > "$W/new.fail"
    grep -E '^FAIL' "$W/head.log" | sort -u > "$W/head.fail"
    new=$(comm -23 "$W/new.fail" "$W/head.fail")
    # A timed-out tree run holds only the FAILs before the hang: the rest never
    # ran, so it is a regression unless HEAD timed out too. No FAIL lines but a
    # nonzero rc (crash, FATAL) is a regression if HEAD ran clean.
    to() { [ "$1" -eq 124 ] || [ "$1" -eq 137 ]; }
    if [ -n "$new" ] || { to $new_rc && ! to $head_rc; } || { [ ! -s "$W/new.fail" ] && [ $head_rc -eq 0 ]; }; then
      { echo "STOP GATE (ouroboros): self-host suite regressed against HEAD:"
        [ -n "$new" ] && echo "$new" | head -15 || tail -8 "$W/new.log"
        echo "  (run: EIGS=$EIG bash test/run.sh)"; } >&2
      fail=1
    else
      # stdout JSON, not stderr: a Stop hook's stderr on exit 0 reaches neither
      # the model nor the user, and a pass-through must not be silent.
      jq -nc --arg m "ouroboros stop gate: self-host suite fails, but every FAIL is shared with HEAD ($(wc -l < "$W/new.fail") line(s)) — pre-existing, not blocking." '{systemMessage: $m}'
    fi
  fi
fi

if [ $AOT -eq 1 ]; then
  # Sample: deterministic per tree state (seeded by the diff), so a re-stop on
  # the same edit re-checks the same programs, and a new edit rotates them.
  seed=$(git diff HEAD -- aot src | sha1sum | cut -c1-8)
  mapfile -t progs < <(ls aot/test/t*.eigs | awk -v s=$((16#$seed)) 'BEGIN{srand(s)} {print rand() "\t" $0}' | sort | cut -f2 | head -"${OURO_GATE_SAMPLE:-16}")
  [ ${#progs[@]} -gt 0 ] || { echo "STOP GATE (ouroboros): no aot/test programs found — the AOT check examined nothing." >&2; exit 2; }
  command -v gcc >/dev/null || { echo "STOP GATE (ouroboros): gcc not found — the AOT C check cannot run." >&2; exit 2; }
  # The file aot_rt.h includes, not just "a src/ dir": /usr/local/src exists empty on Ubuntu.
  [ -f "$EIGS_DIR/src/eigenscript.h" ] || { echo "STOP GATE (ouroboros): no $EIGS_DIR/src/eigenscript.h — EIGS_DIR does not point at an EigenScript checkout, so the generated C cannot be checked." >&2; exit 2; }
  DEFS="-DEIGENSCRIPT_EXT_HTTP=0 -DEIGENSCRIPT_EXT_MODEL=0 -DEIGENSCRIPT_EXT_DB=0"
  checked=0; head_ok=0; cc_both=0; regress=""
  for p in "${progs[@]}"; do
    if [ "$(left)" -lt 30 ]; then
      echo "STOP GATE (ouroboros): out of time after $head_ok program(s) — the transpiler or the suite is slow or hanging. Run: bash aot/test/run.sh" >&2; exit 2
    fi
    b=$(basename "$p" .eigs)
    ( cd "$ROOT/aot" && timeout 20 "$EIG" compile.eigs "$ROOT/$p" "$EIGS_DIR" ) > "$W/$b.new.c" 2>"$W/$b.new.err"; rn=$?
    ( cd "$W/aot" && timeout 20 "$EIG" compile.eigs "$ROOT/$p" "$EIGS_DIR" ) > "$W/$b.head.c" 2>/dev/null; rh=$?
    [ $rh -eq 0 ] && head_ok=$((head_ok + 1))
    if [ $rn -eq 124 ] && [ $rh -ne 124 ]; then   # ~0.3 s normally: a 20 s hang is the change's
      regress="$regress\n  $p: the working tree's transpiler HANGS (>20 s); HEAD finishes (rc=$rh)"
      break
    fi
    if [ $rh -eq 0 ] && [ $rn -ne 0 ]; then
      regress="$regress\n  $p: HEAD transpiles it, the working tree fails (rc=$rn): $(head -c 300 "$W/$b.new.err")"
      continue
    fi
    [ $rn -eq 0 ] || continue
    if ! cmp -s "$W/$b.new.c" "$W/$b.head.c" || dirty aot/aot_rt.h; then
      [ $checked -lt "${OURO_GATE_CC:-3}" ] || continue
      checked=$((checked + 1))
      if ! gcc -fsyntax-only $DEFS -I"$ROOT/aot" -I"$EIGS_DIR/src" "$W/$b.new.c" 2>"$W/$b.cc.err"; then
        if [ $rh -eq 0 ] && gcc -fsyntax-only $DEFS -I"$W/aot" -I"$EIGS_DIR/src" "$W/$b.head.c" 2>/dev/null; then
          regress="$regress\n  $p: generated C no longer compiles: $(grep -m2 'error' "$W/$b.cc.err")"
        else
          cc_both=$((cc_both + 1))
        fi
      fi
    fi
  done
  # Vacuity: a gate that examined nothing must not read as a pass.
  if [ -z "$regress" ] && [ $head_ok -eq 0 ]; then
    echo "STOP GATE (ouroboros): HEAD transpiled 0 of ${#progs[@]} sampled programs — the AOT check examined nothing (EIGS_DIR=$EIGS_DIR wrong?)." >&2; exit 2
  fi
  if [ -z "$regress" ] && [ $checked -gt 0 ] && [ $cc_both -eq $checked ]; then
    echo "STOP GATE (ouroboros): gcc rejects HEAD's generated C too ($cc_both of $checked) — the C check examined nothing (headers under $EIGS_DIR/src?)." >&2; exit 2
  fi
  if [ -n "$regress" ]; then
    { echo "STOP GATE (ouroboros): AOT transpiler regressed against HEAD on ${#progs[@]} sampled aot/test programs:"
      printf '%b\n' "$regress"
      echo "  (behavioural parity is aot/test/run.sh's job; this gate only catches crashes and invalid C)"; } >&2
    fail=1
  fi
fi

[ $fail -eq 0 ] || exit 2
exit 0
