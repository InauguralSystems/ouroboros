#!/bin/bash
# AOT differential harness: native output must equal the VM oracle, byte-for-byte.
set -uo pipefail
cd "$(dirname "$0")/.."
EIG="${EIGS:-../../EigenScript/src/eigenscript}"
# Check orchestration/refusal/aggregate semantics with synthetic tiers before
# any compiler builds. Missing Python, missing tests or a failed check is fatal.
if ! PYTHONDONTWRITEBYTECODE=1 python3 test/test_gate_tiers.py; then
  echo 'FAIL: seven-tier gate driver selftests' >&2
  exit 1
fi
if ! PYTHONDONTWRITEBYTECODE=1 python3 test/test_slot_index_gate.py; then
  echo 'FAIL: slot index ownership gate selftests' >&2
  exit 1
fi
if ! PYTHONDONTWRITEBYTECODE=1 python3 test/slot_index_emission.py --selftest; then
  echo 'FAIL: slot index emission selftests' >&2
  exit 1
fi
if ! EIGS="$EIG" PYTHONDONTWRITEBYTECODE=1 python3 test/slot_index_emission.py; then
  echo 'FAIL: slot index emission coverage' >&2
  exit 1
fi
if ! PYTHONDONTWRITEBYTECODE=1 python3 test/slot_scalar_emission.py --selftest; then
  echo 'FAIL: slot scalar emission selftests' >&2
  exit 1
fi
if ! EIGS="$EIG" PYTHONDONTWRITEBYTECODE=1 python3 test/slot_scalar_emission.py; then
  echo 'FAIL: slot scalar emission coverage' >&2
  exit 1
fi
# (#1361) The AOT-owned strict sites, DERIVED from aot_rt.h, never listed: each
# definition whose body reads g_strict, closed over the header definitions that
# call one (a macro-built function through its macro). Comments are stripped
# first. A `_strict` fixture's generated C must call at least one of them
# (these include num_guard and aot_add, so any float arithmetic satisfies it).
rt_src=$(awk '{ line = $0
  if (inc) { if (!sub(/.*\*\//, "", line)) next; inc = 0 }
  gsub(/\/\*([^*]|\*+[^*\/])*\*+\//, "", line); sub(/\/\/.*/, "", line); if (sub(/\/\*.*/, "", line)) inc = 1
  print line }' aot_rt.h)
strict_sites=$(printf '%s\n' "$rt_src" | awk '{ line = $0 }
line ~ /^(static |#define [A-Za-z_0-9]+\(|[A-Z_]+\([a-z_0-9]+,)/ {
  h = line; gsub(/__attribute__\(\([^)]*\)\)/, "", h); sub(/^#define /, "", h)
  if (h ~ /^[A-Z_]+\([a-z_0-9]+,/) { nm = h; sub(/^[A-Z_]+\(/, "", nm); sub(/,.*/, "", nm); m = h; sub(/\(.*/, "", m); body[nm] = body[nm] " " m "("; nm = ""; next }
  sub(/\(.*/, "", h); n = split(h, w, /[^A-Za-z_0-9]+/); nm = w[n] }
nm != "" { body[nm] = body[nm] " " line }
END { for (d in body) if (body[d] ~ /[^A-Za-z_0-9]g_strict[^A-Za-z_0-9]/) set[d] = 1
  do { grew = 0; for (d in body) if (!(d in set)) for (s in set) if (index(body[d], s "(")) { set[d] = 1; grew = 1; break } } while (grew)
  for (s in set) print s }' | paste -sd'|' -)
[ -n "$strict_sites" ] || { echo 'FAIL: derived ZERO strict sites from aot_rt.h (no definition reads g_strict)'; exit 1; }
# (#1361) No call made from compiled code changes g_trace_current_line
# (aot_rt.h, above AOT_FOREIGN). Direct compiled calls restore through the
# AOT_RESTAMP macro compile.eigs emits beside every prototype. Every other
# exit is enumerated by gcc, not by spelling: the call graph of aot_rt.h
# (every static and inline definition kept) lists each indirect call and each
# direct call of call_eigs_fn or a runtime builtin_*, located at its outermost
# macro expansion, and the source there must begin `AOT_FOREIGN(`. The C that
# compile.eigs emits reaches the runtime only through aot_rt.h, so its code
# lines may not name an exit at all. Not seen: an exit inside a macro body
# aot_rt.h never expands, and a direct call of a runtime entry named otherwise.
fxd=$(mktemp -d); printf '#include "aot_rt.h"\n' > "$fxd/fx.c"
gcc -O0 ${AOT_ARCH:--march=native} -fkeep-static-functions -fkeep-inline-functions -fcallgraph-info -I"$PWD" \
  -I"${EIGS_DIR:-../../EigenScript}/src" -c "$fxd/fx.c" -o "$fxd/fx.o" 2>"$fxd/err" && [ -s "$fxd/fx.ci" ] ||
  { cat "$fxd/err"; echo 'FAIL: line-restore: no call graph of aot_rt.h (gcc -fcallgraph-info)'; exit 1; }
fx=$( { awk -v h="$PWD/aot_rt.h" 'NR == FNR { src[FNR] = $0; next }
/^edge: .*sourcename: "[^"]*:eigs_call_builtin" .*targetname: "__indirect_call"/ { next }
/^edge: .*targetname: "(__indirect_call|call_eigs_fn|builtin_[a-z_0-9]+)"/ {
  loc = $0; sub(/.*label: "/, "", loc); sub(/".*/, "", loc); split(loc, p, ":")
  n++; if (p[1] != h || substr(src[p[2]], p[3], 12) != "AOT_FOREIGN(") print "FAIL: call leaves compiled code outside AOT_FOREIGN: " loc ": " src[p[2]] }
END { print n + 0 }' aot_rt.h "$fxd/fx.ci"
  grep -nE '(data\.builtin|call_eigs_fn|(^|[^A-Za-z_0-9])builtin_[a-z_0-9]+)' compile.eigs | grep -vE '^[0-9]+:[[:space:]]*#' | sed 's/^/FAIL: compile.eigs names an exit from compiled code: /'
  grep -nE 'eigs_call_builtin[[:space:]]*\(' aot_rt.h compile.eigs | grep -vE '^[^:]+:[0-9]+:[[:space:]]*(#|/?\*)' | sed 's/^/FAIL: eigs_call_builtin called from the AOT (its body is exempt above; use AOT_GATED inside AOT_FOREIGN): /'; } )
rm -rf "$fxd"; printf '%s\n' "$fx" | grep '^FAIL'; fxn=$(printf '%s\n' "$fx" | grep -v '^FAIL' | tail -1)
fxb=$(printf '%s\n' "$fx" | grep -c '^FAIL'); [ "$fxn" -gt 0 ] && [ "$fxb" -eq 0 ] ||
  { echo "FAIL: line-restore sites: $fxn examined, $fxb outside AOT_FOREIGN"; exit 1; }
echo "--- line-restore: $fxn call(s) leaving compiled code, all inside AOT_FOREIGN ---"
strict_n=0
fail=0
for prog in test/*.eigs; do
  name=$(basename "$prog")
  # `_`-prefixed files are companion MODULES for import fixtures, not tests.
  case "$name" in _*) continue;; *_strict.eigs) strict_n=$((strict_n + 1));; esac
  bin=$(mktemp /tmp/aot_test.XXXXXX)
  why=""
  if ! bash build.sh "$prog" "$bin" >/tmp/aot_build.log 2>&1; then
    echo "BUILD FAIL: $name"; tail -3 /tmp/aot_build.log; fail=1; rm -f "$bin"; continue
  fi
  # BOTH sides run under a timeout. Neither did, and on 2026-08-23 a built
  # test binary hung and was still spinning a full core 3.8 days later --
  # orphaned to init, invisible to this harness, and slowing every other job
  # on the box until it was found by accident. An AOT that loops where the VM
  # terminates is precisely the miscompile class this suite exists to catch,
  # so a hang must be a loud FAIL, not an unbounded wait: `timeout` exits 124,
  # which the exit-code contract below already reads as a divergence.
  AOT_TEST_TIMEOUT="${AOT_TEST_TIMEOUT:-300}"
  ref=$(timeout "$AOT_TEST_TIMEOUT" "$EIG" "$prog" 2>&1); ref_rc=$?
  got=$(timeout "$AOT_TEST_TIMEOUT" "$bin" 2>&1); got_rc=$?
  [ "$ref_rc" -eq 124 ] && why="VM reference TIMED OUT after ${AOT_TEST_TIMEOUT}s"
  [ "$got_rc" -eq 124 ] && why="AOT binary TIMED OUT after ${AOT_TEST_TIMEOUT}s — it does not terminate where the VM does"
  match=0
  case "$name" in
    *_tol.eigs)
      # Association-unspecified reductions (dot): the AOT reassociates the sum
      # across SIMD lanes, so it agrees with the VM within tolerance, NOT byte
      # for byte (FP add is non-associative — by design, per the `dot` spec).
      VM="$ref" AOT="$got" python3 - <<'PY' && match=1
import os, re, sys
NUM = r'-?\d+\.?\d*(?:[eE][-+]?\d+)?'
def nums(s): return re.findall(NUM, s)
# (#104) everything that is NOT a number must match byte for byte: with the
# numeric tokens masked, the two outputs are compared as text, so a label,
# a string, or an error line that differs is a failure -- the old comparator
# saw only the number sequences and let any surrounding text diverge.
if re.sub(NUM, '#', os.environ['VM']) != re.sub(NUM, '#', os.environ['AOT']): sys.exit(1)
a, b = nums(os.environ['VM']), nums(os.environ['AOT'])
if len(a) != len(b): sys.exit(1)
for x, y in zip(a, b):
    fx, fy = float(x), float(y)
    if abs(fx - fy) > 1e-9 * max(1.0, abs(fx)): sys.exit(1)
sys.exit(0)
PY
      ;;
    *_die.eigs)
      # Programs the AOT stops with its OWN fatal diagnostic. `aot_num_ck_at`
      # and friends are not rt_error raises — they print a native message and
      # exit(1) — so their text legitimately differs from the VM's
      # operator-specific raise ("non-numeric value in a numeric context at f:
      # name z (type str)" vs "cannot apply '*' to str and num"). That put the
      # ENTIRE aot_num_ck_at family outside _err, and a blind critic proved the
      # consequence: replacing a checked arm with `return 0.0` — converting a
      # loud death into a run-past-error (#96) — left the whole suite green.
      #
      # What is still semantics rather than presentation: everything printed
      # BEFORE the death, and the death itself. So compare stdout exactly and
      # require equal nonzero exit codes; do not compare the message. A
      # gutted check shows up twice over — extra stdout AND rc 0.
      #
      # Use _err whenever the messages DO match; this class is strictly for
      # diagnostics the AOT owns. Re-runs capture stdout alone, so it costs an
      # extra pair of runs, paid only by fixtures in this class.
      ref_out=$(timeout "$AOT_TEST_TIMEOUT" "$EIG" "$prog" 2>/dev/null)
      got_out=$(timeout "$AOT_TEST_TIMEOUT" "$bin" 2>/dev/null)
      [ "$ref_out" = "$got_out" ] && match=1
      ;;
    *_err.eigs)
      # Programs that RAISE. The AOT cannot reproduce the VM's uncaught-error
      # diagnostic: the source excerpt + column caret are added by the VM's
      # CHECK_ERROR from the failing instruction's bytecode offset (#407), and
      # the `at <frame>` trace comes from vm_print_stack_trace walking VM
      # frames — neither exists in a native binary. The AOT also has no line
      # info unless the program is traced (the emitter stamps
      # g_trace_current_line only under g_traced), so it reports line 0.
      #
      # What must still match EXACTLY is the part that is semantics rather
      # than presentation: everything the program printed before it died, and
      # the error's kind and message text. That is the property this class
      # exists to defend — an AOT that RUNS PAST a line the VM stops at is the
      # silent-wrong-answer bug (ouroboros#96), and it is caught here.
      #
      # Normalization: drop the VM-only excerpt/caret/`at ...` lines and blank
      # the line number in the `Error line N:` frame. Nothing else is touched,
      # so a divergence in message text, error kind, ordering, or any stdout
      # line still fails. Diagnostic parity itself is tracked separately —
      # when the AOT can site its errors, delete this class and re-diff.
      # (round 140, #201; unconditional since the round-147 pin bump) the
      # LINE NUMBER is compared: every program stamps g_trace_current_line
      # per statement, every AOT helper reports through it, and since
      # EigenScript#1082 a builtin's line-0 raise with no live VM frame
      # reports that stamp too. The VM's source excerpt and its
      # `at <frame> (line N)` trace lines are dropped (the AOT does not
      # print them).
      norm() {
        printf '%s\n' "$1" \
          | grep -vE '^\s+[0-9]+ \||^\s+\|.*\^|^  at '
      }
      [ "$(norm "$ref")" = "$(norm "$got")" ] && match=1
      ;;
    *) [ "$ref" = "$got" ] && match=1 ;;
  esac
  # Exit-code contract (#101): text alone is spoofable — a VM-errors/AOT-
  # exits-0 pair with matching normalized text sailed through here, which is
  # the run-past-error class this suite exists to catch. Normal and _tol
  # cases need a CLEAN nonempty reference and equal exit codes; _err cases
  # need BOTH sides to die (a VM that starts exiting 0 means the class label
  # is stale — fail loud, don't quietly re-purpose the normalizer). The class
  # markers are filename SUFFIXES (`*_err.eigs`), not substrings: the old
  # `*_err*` glob silently put t59_sited_errors — a clean rc-0 program — in
  # the raising class, where the normalizer could have eaten a real
  # divergence. Since #103 the _err death codes are compared for EQUALITY:
  # the AOT dies by a clean exit(1) on every uncaught runtime error (the old
  # SIGSEGV-after-the-message rc-139 teardown was a NULL-VM deref inside
  # rt_error's stack-trace walk, fixed in aot_rt.h), which is exactly the
  # VM's uncaught-error exit code.
  if [ "$match" -eq 1 ]; then
    case "$name" in
      *_die.eigs)
        if [ "$ref_rc" -eq 0 ]; then
          match=0; why="_die case but the VM exits 0 — stale class, drop the suffix"
        elif [ "$got_rc" -eq 0 ]; then
          match=0; why="VM errors (rc=$ref_rc) but the AOT exits 0 — runs past the error"
        elif [ "$got_rc" -ne "$ref_rc" ]; then
          match=0; why="death codes differ (VM $ref_rc, AOT $got_rc)"
        fi ;;
      *_err.eigs)
        if [ "$ref_rc" -eq 0 ]; then
          match=0; why="_err case but the VM exits 0 — stale class, drop the suffix"
        elif [ "$got_rc" -eq 0 ]; then
          match=0; why="VM errors (rc=$ref_rc) but the AOT exits 0 — runs past the error"
        elif [ "$got_rc" -ne "$ref_rc" ]; then
          match=0; why="death codes differ (VM $ref_rc, AOT $got_rc) — #103 contract: uncaught errors exit cleanly with the VM's code"
        fi ;;
      *)
        if [ "$ref_rc" -ne 0 ]; then
          match=0; why="VM reference errors (rc=$ref_rc) — declare *_err.eigs or fix the program"
        elif [ -z "$ref" ]; then
          match=0; why="VM reference output is empty — a parity claim needs a nonempty reference"
        elif [ "$got_rc" -ne "$ref_rc" ]; then
          match=0; why="exit codes differ (VM $ref_rc, AOT $got_rc)"
        fi ;;
    esac
  fi
  # (#1361) `*_strict.eigs`: the fixture of an AOT site that mirrors the
  # runtime's strict flag. The ambient run above holds whatever the runtime's
  # default is; these also pin both halves explicitly, on the SAME binary --
  # the flag is read at run time, never baked in -- and each pair must be
  # byte-equal with rc 0 (the fixtures catch their raises). A fixture whose
  # VM output does not move between =1 and =0 reaches no strict site: FAIL.
  if [ "$match" -eq 1 ]; then
    case "$name" in *_strict.eigs)
      s_vm1=""
      for sm in 1 0; do
        s_ref=$(EIGS_STRICT=$sm timeout "$AOT_TEST_TIMEOUT" "$EIG" "$prog" 2>&1); s_rrc=$?
        s_got=$(EIGS_STRICT=$sm timeout "$AOT_TEST_TIMEOUT" "$bin" 2>&1); s_grc=$?
        if [ "$s_rrc" -ne 0 ] || [ "$s_grc" -ne 0 ] || [ "$s_ref" != "$s_got" ]; then
          match=0; why="EIGS_STRICT=$sm: VM rc $s_rrc, AOT rc $s_grc, outputs $([ "$s_ref" = "$s_got" ] && echo equal || echo differ)"
          ref="$s_ref"; got="$s_got"; break
        fi
        [ "$sm" = 1 ] && s_vm1="$s_ref"
      done
      if [ "$match" -eq 1 ] && [ "$s_vm1" = "$s_ref" ]; then
        match=0; why="_strict fixture prints the same on the VM under EIGS_STRICT=1 and =0 -- it reaches no strict site"
      fi
      # The VM moving proves A strict site ran, not an AOT one: a runtime
      # builtin's own raise moves it too. Tested: the generated C calls an AOT
      # strict-decision helper. It cannot tell whose raise fired.
      if [ "$match" -eq 1 ]; then
        s_gen=$(timeout 120 "$EIG" compile.eigs "$prog" "${EIGS_DIR:-../../EigenScript}" 2>&1); s_trc=$?
        if [ "$s_trc" -ne 0 ] || ! printf '%s\n' "$s_gen" | grep -qE "(^|[^A-Za-z_0-9])($strict_sites)\("; then
          match=0; why="_strict fixture's generated C (transpile rc $s_trc) calls no AOT strict-decision helper"
        fi
      fi ;;
    esac
  fi
  if [ "$match" -eq 1 ]; then
    echo "PASS: $name"
  else
    echo "FAIL: $name${why:+ ($why)}"
    echo "  VM:  $(printf '%s' "$ref" | tr '\n' '|')"
    echo "  AOT: $(printf '%s' "$got" | tr '\n' '|')"
    fail=1
  fi
  rm -f "$bin"
done
# A FLOOR on the _strict class (the count at #1361 round 5), not a pin: it may
# grow; with fewer, a fixture was lost or renamed out of the class.
if [ "$strict_n" -lt 17 ]; then
  echo "FAIL: _strict class examined $strict_n fixture(s), floor 17"; fail=1
else
  echo "--- _strict class: $strict_n fixture(s) examined ---"
fi
# ---- bench transpile-check tier (#109) --------------------------------
# Every aot/bench/*.eigs must BUILD (rc=0). The benches are the #64/F-OURO-25
# forcing-function corpus and nothing else covered them: the #105/#107
# refusal train broke both checksum benches invisibly. Running them is NOT
# gated here (minutes of wall time on the 50-pass workloads); this tier only
# proves the envelope still admits them. Population comes from the glob
# itself — zero matches is a FAIL, not a vacuous pass (a renamed/moved
# bench dir must go red, never green-by-absence).
bench_n=0
for prog in bench/*.eigs; do
  [ -e "$prog" ] || continue
  bench_n=$((bench_n + 1))
  name=$(basename "$prog")
  bin=$(mktemp /tmp/aot_bench.XXXXXX)
  if bash build.sh "$prog" "$bin" >/tmp/aot_bench_build.log 2>&1; then
    echo "PASS: bench build $name"
  else
    echo "FAIL: bench build $name (transpile/compile rc!=0 — the AOT envelope no longer admits its own bench corpus)"
    tail -3 /tmp/aot_bench_build.log
    fail=1
  fi
  rm -f "$bin"
done
if [ "$bench_n" -eq 0 ]; then
  echo "FAIL: bench tier examined ZERO programs (bench/*.eigs matched nothing — the gate is vacuous)"
  fail=1
else
  echo "--- bench tier: $bench_n program(s) build-checked ---"
fi
# ---- load_file shadow arm (ouroboros#147) ---------------------------------
# The parity loop above runs every fixture from aot/ with the fixture beside
# its dependencies, so it can never observe a cwd-vs-file-directory
# disagreement. This arm builds the #147 A/B layout in a temp dir and runs
# both sides from the SHADOWING cwd (test/load_file_shadow.sh).
if EIGS="$EIG" bash test/load_file_shadow.sh; then :; else fail=1; fi
# ---- --dump-inference agreement gate (#126 follow-up) ---------------------
# The tiers above adjudicate what a fixture PRINTS. The compiler's own storage
# report (--dump-inference, #65) prints nothing at run time and had no test at
# all, so it drifted silently when #126 made the observer gate per-name. This
# arm joins the dump against the C the SAME transpile emits, for a fixture with
# both an observed and an un-observed numeric name at each scope. Two
# transpiles and a grep per fixture -- no gcc. Its own header states which dump
# fields it adjudicates and which it does not.
if EIGS="$EIG" bash test/dump_inference_gate.sh; then :; else fail=1; fi
# ---- REFUSAL TIER (ouroboros#112) ----------------------------------------
# The parity tiers above can only assert that a program COMPILES AND MATCHES.
# A program the AOT is supposed to REJECT reads to them as BUILD FAIL, so every
# loud envelope guard in the compiler had no test at all — and it showed: two
# consecutive blind-critic rounds on the `import` work found real defects in
# guards, one of them a silent wrong answer against the real stdlib, with the
# whole 108-program suite green through both.
#
# Each test/refuse/*.eigs carries `# EXPECT: <substring>` on a line of its own.
# The contract is three-part, and the third part is what makes it a test of the
# ENVELOPE rather than of a broken program:
#   1. the AOT must REFUSE it (transpile exits nonzero);
#   2. the refusal must CONTAIN the expected substring — a guard that fires for
#      the wrong reason is not a passing guard;
#   3. the VM must RUN it (rc 0). Otherwise the program is simply invalid and
#      proves nothing about the AOT's envelope.
# Zero matches is a FAIL, as in the bench tier: a moved directory must go red,
# never green-by-absence.
# EIG may be a relative path, and the VM check below runs from the test's own
# directory — so resolve it once, absolutely. Getting this wrong produced
# rc=127 for every test and a tier that failed for the wrong reason.
# ...and it must resolve BOTH ways EIG is supplied. A path (the local
# default, "../../EigenScript/src/eigenscript") resolves by dirname; a BARE
# NAME resolves on PATH, which is what CI passes -- the devcontainer removes
# the source tree after `make install`, so tests.yml runs `EIGS=eigenscript`.
# dirname of a bare name is ".", so the old line built "<cwd>/eigenscript",
# which does not exist, and every refusal guard failed rc=127 IN CI ONLY for
# 15+ consecutive runs while the local suite stayed green. The comment above
# was written by the round that hit the rc=127 on the path axis and fixed
# only that axis -- a guard tested on one of its two inputs.
case "$EIG" in
  */*) EIG_ABS=$(cd "$(dirname "$EIG")" && pwd)/$(basename "$EIG") ;;
  *)   EIG_ABS=$(command -v "$EIG" || true) ;;
esac
if [ -z "$EIG_ABS" ] || [ ! -x "$EIG_ABS" ]; then
  echo "FAIL: refusal tier cannot resolve the VM from EIGS='$EIG'"
  echo "      (a path is resolved by dirname, a bare name on PATH)"
  exit 1
fi
refuse_n=0
for prog in test/refuse/*.eigs; do
  [ -e "$prog" ] || continue
  name=$(basename "$prog")
  # `_`-prefixed files are companion MODULES for the tests, not tests.
  case "$name" in _*) continue;; esac
  refuse_n=$((refuse_n + 1))
  want=$(grep -m1 '^# EXPECT:' "$prog" | sed 's/^# EXPECT:[[:space:]]*//')
  if [ -z "$want" ]; then
    echo "FAIL: refuse $name (no '# EXPECT:' line — the tier cannot tell a correct refusal from any refusal)"
    fail=1; continue
  fi
  # (3) the VM must accept it, from the program's own directory so its
  # load_file/import resolution matches what the AOT sees.
  ( cd "$(dirname "$prog")" && timeout 60 "$EIG_ABS" "$(basename "$prog")" ) >/dev/null 2>&1
  vm_rc=$?
  if [ "$vm_rc" -ne 0 ]; then
    echo "FAIL: refuse $name (the VM does not run it, rc=$vm_rc — this tests a broken program, not the envelope)"
    fail=1; continue
  fi
  EIGS_DIR="${EIGS_DIR:-../../EigenScript}" timeout 120 "$EIG" compile.eigs "$prog" "${EIGS_DIR:-../../EigenScript}" >/dev/null 2>/tmp/aot_refuse.log
  if [ $? -eq 0 ]; then
    echo "FAIL: refuse $name (the AOT ACCEPTED a program it must reject — the guard is gone or unreachable)"
    fail=1
  elif ! grep -qF "$want" /tmp/aot_refuse.log; then
    echo "FAIL: refuse $name (refused, but not for the stated reason)"
    echo "  want: $want"
    echo "  got : $(grep -m1 -v '^[[:space:]]*$' /tmp/aot_refuse.log | cut -c1-100)"
    fail=1
  else
    echo "PASS: refuse $name"
  fi
done
if [ "$refuse_n" -eq 0 ]; then
  echo "FAIL: refusal tier examined ZERO programs (test/refuse/*.eigs matched nothing — the gate is vacuous)"
  fail=1
else
  echo "--- refusal tier: $refuse_n guard(s) exercised ---"
fi

# Runtime refusals (round 189, #188): programs the AOT must BUILD (the
# construct is out of the compiled unit's sight -- a module loaded at run time)
# and the VM runs at rc 0, but whose compiled binary must die NAMING the
# reason. Each test/rtrefuse/*.eigs carries `# EXPECT: <substring>`; the binary
# must exit nonzero and its stderr must contain it. A silent-wrong residual
# (rc 0, wrong output) fails here by rc; a death for a different reason fails
# by text. Runs with cwd = test/, like the fixtures above.
rtrefuse_n=0
# The VM runs with cwd = test/, so it needs $EIG_ABS (resolved above for the
# refusal tier, which also handles the on-PATH `EIGS=eigenscript` CI uses): the
# first two runs of this arm reported rc 127 -- once for a relative path, once
# for a bare command name run through dirname/pwd.
for prog in test/rtrefuse/*.eigs; do
  [ -f "$prog" ] || continue
  name=$(basename "$prog" .eigs)
  rtrefuse_n=$((rtrefuse_n + 1))
  want=$(grep -m1 '^# EXPECT:' "$prog" | sed 's/^# EXPECT:[[:space:]]*//')
  if [ -z "$want" ]; then
    echo "FAIL: rtrefuse $name (no '# EXPECT:' line)"; fail=$((fail + 1)); continue
  fi
  ( cd test && timeout 30 "$EIG_ABS" "rtrefuse/$name.eigs" >/dev/null 2>&1 ); vm_rc=$?
  if [ "$vm_rc" -ne 0 ]; then
    echo "FAIL: rtrefuse $name (the VM does not run it, rc=$vm_rc — this tests a broken program, not the residual)"; fail=$((fail + 1)); continue
  fi
  if ! bash build.sh "$prog" /tmp/aot_rtrefuse_bin >/tmp/aot_rtrefuse_build.log 2>&1; then
    echo "FAIL: rtrefuse $name (the AOT REFUSED at build time — the construct is in sight; this belongs in test/refuse)"; fail=$((fail + 1)); continue
  fi
  ( cd test && timeout 30 /tmp/aot_rtrefuse_bin >/tmp/aot_rtrefuse_out.log 2>/tmp/aot_rtrefuse_err.log ); bin_rc=$?
  if [ "$bin_rc" -eq 124 ] || [ "$bin_rc" -eq 137 ]; then
    echo "FAIL: rtrefuse $name (binary timed out / killed, rc=$bin_rc)"; fail=$((fail + 1))
  elif [ "$bin_rc" -eq 0 ]; then
    echo "FAIL: rtrefuse $name (the binary exited 0 — the residual is still silent: $(head -c 80 /tmp/aot_rtrefuse_out.log | tr '\n' '|'))"; fail=$((fail + 1))
  elif ! grep -qF "$want" /tmp/aot_rtrefuse_err.log; then
    echo "FAIL: rtrefuse $name (died, but not for the stated reason)"
    echo "  want: $want"; echo "  got : $(grep -m1 -v '^[[:space:]]*$' /tmp/aot_rtrefuse_err.log | cut -c1-120)"; fail=$((fail + 1))
  else
    echo "PASS: rtrefuse $name"
  fi
done
if [ "$rtrefuse_n" -eq 0 ]; then
  echo "FAIL: runtime-refusal tier examined ZERO programs (test/rtrefuse/*.eigs matched nothing — the gate is vacuous)"; fail=$((fail + 1))
else
  echo "--- runtime-refusal tier: $rtrefuse_n residual(s) exercised ---"
fi

[ "$fail" -eq 0 ] && echo "--- all AOT parity tests passed ---" || exit 1
