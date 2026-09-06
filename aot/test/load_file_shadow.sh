#!/bin/bash
# ouroboros#147: literal load_file resolution is a property of the FILE, not
# of the process working directory. The issue's exact layout, driven from a
# temp dir and run from the SHADOWING cwd on both sides:
#   A/inc.eigs   print of "SCRIPTDIR-COPY"        A/prog.eigs  load_file of "inc.eigs"
#   B/inc.eigs   print of "CWD-COPY"              cd B && eigenscript ../A/prog.eigs
# Before EigenScript#1056 (pin c1684bc) the VM resolved cwd-first and printed
# CWD-COPY while the spliced binary printed SCRIPTDIR-COPY -- both rc 0, a
# silent wrong answer. Since v0.43.0 both print SCRIPTDIR-COPY. The layout
# also shadows the subdir and project-root (eigs.json) steps, so a splicer
# that consulted the cwd at ANY step goes red here.
#
# Four assertions, each with a stated failure mode:
#   1. the shadow is LIVE: a control program in B loading "inc.eigs" prints
#      CWD-COPY on the VM (a deleted/renamed shadow makes the diff vacuous);
#   2. the VM, run from B, prints the A copies (the pin's rule -- an oracle
#      that regressed to cwd-first fails here, by name);
#   3. the native binary, run from B, is byte-identical to the VM (rc too);
#   4. (round 2) the STDLIB step reached from a spliced child is not
#      base-independent: C/lib/test.eigs shadows the stdlib's for a load
#      from C, C/sub/child.eigs loads "lib/test.eigs" (from sub/ the chain
#      ends at the stdlib -- no eigs.json above the temp dir), and the
#      native binary, run from C itself (no cwd trick needed), must match
#      the VM. A splicer that left the child's literal as a runtime call
#      loaded C's decoy from the baked main directory instead: VM
#      `child-done|main-done` rc 0 vs AOT `MAIN-DIR-LIB-TEST|undefined
#      variable 'assert_eq'` rc 1 (and both rc 0 without the assert_eq
#      call -- the silent-wrong class). The C/control.eigs check keeps the
#      decoy provably live on the VM.
set -uo pipefail
cd "$(dirname "$0")/.."
EIG="${EIGS:-../../EigenScript/src/eigenscript}"
case "$EIG" in */*) EIG_ABS=$(cd "$(dirname "$EIG")" && pwd)/$(basename "$EIG");; *) EIG_ABS=$(command -v "$EIG" || true);; esac
[ -x "$EIG_ABS" ] || { echo "FAIL: load_file_shadow cannot resolve the VM from EIGS='$EIG'"; exit 1; }
T=$(mktemp -d /tmp/aot_lf_shadow.XXXXXX)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/A/sub" "$T/A/lib" "$T/B/sub" "$T/B/lib"
printf 'print of "SCRIPTDIR-COPY"\n' > "$T/A/inc.eigs"
printf 'print of "CWD-COPY"\n'       > "$T/B/inc.eigs"
printf 'print of "SCRIPTDIR-SUB"\nload_file of "lib/root_helper.eigs"\n' > "$T/A/sub/deep.eigs"
printf 'print of "CWD-SUB"\n'       > "$T/B/sub/deep.eigs"
printf 'print of "SCRIPTDIR-LIB"\ndefine helper(n) as:\n    return n + 1\n' > "$T/A/lib/root_helper.eigs"
printf 'print of "CWD-LIB"\ndefine helper(n) as:\n    return n - 1\n'       > "$T/B/lib/root_helper.eigs"
printf '{"name": "lf147", "version": "0.0.0", "deps": {}}\n' > "$T/A/eigs.json"
printf 'load_file of "inc.eigs"\nload_file of "sub/deep.eigs"\nprint of (helper of 20)\n' > "$T/A/prog.eigs"
printf 'load_file of "inc.eigs"\n' > "$T/B/control.eigs"
fail=0
# 1. the shadow is live
ctl=$(cd "$T/B" && timeout 60 "$EIG_ABS" control.eigs 2>&1)
if [ "$ctl" != "CWD-COPY" ]; then
  echo "FAIL: load_file_shadow (the B/inc.eigs shadow is not live on the VM: got '$ctl' -- the diff below would be vacuous)"; fail=1
fi
# 2. the VM from B resolves the A copies (file-relative + project root, no cwd step)
want=$(printf 'SCRIPTDIR-COPY\nSCRIPTDIR-SUB\nSCRIPTDIR-LIB\n21')
ref=$(cd "$T/B" && timeout 60 "$EIG_ABS" ../A/prog.eigs 2>&1); ref_rc=$?
if [ "$ref" != "$want" ] || [ "$ref_rc" -ne 0 ]; then
  echo "FAIL: load_file_shadow (VM from the shadowing cwd did not resolve file-relative: rc=$ref_rc, got '$(printf '%s' "$ref" | tr '\n' '|')')"; fail=1
fi
# 3. the native binary from B is byte-identical. The TRANSPILER runs from B
#    too (AOT_TRANSPILE_CWD, build.sh): a splicer that consulted the cwd at
#    any step would splice the B copies and print CWD-COPY here.
if ! AOT_TRANSPILE_CWD="$T/B" bash build.sh "$T/A/prog.eigs" "$T/prog_bin" >"$T/build.log" 2>&1; then
  echo "FAIL: load_file_shadow (BUILD FAIL)"; tail -3 "$T/build.log"; exit 1
fi
got=$(cd "$T/B" && timeout 60 "$T/prog_bin" 2>&1); got_rc=$?
if [ "$ref" != "$got" ] || [ "$ref_rc" -ne "$got_rc" ]; then
  echo "FAIL: load_file_shadow (VM vs AOT from the shadowing cwd differ: VM rc=$ref_rc '$(printf '%s' "$ref" | tr '\n' '|')' AOT rc=$got_rc '$(printf '%s' "$got" | tr '\n' '|')')"; fail=1
fi
# 4. stdlib step from a spliced child (no eigs.json anywhere above $T/C)
mkdir -p "$T/C/lib" "$T/C/sub"
printf 'print of "MAIN-DIR-LIB-TEST"\n' > "$T/C/lib/test.eigs"
printf 'load_file of "lib/test.eigs"\nassert_eq of [1, 1, "lf147-stdlib"]\nprint of "child-done"\n' > "$T/C/sub/child.eigs"
printf 'load_file of "sub/child.eigs"\nprint of "main-done"\n' > "$T/C/prog.eigs"
printf 'load_file of "lib/test.eigs"\n' > "$T/C/control.eigs"
ctl4=$(cd "$T/C" && timeout 60 "$EIG_ABS" control.eigs 2>&1)
if [ "$ctl4" != "MAIN-DIR-LIB-TEST" ]; then
  echo "FAIL: load_file_shadow (the C/lib/test.eigs stdlib decoy is not live on the VM: got '$ctl4' -- assertion 4 would be vacuous)"; fail=1
fi
want4=$(printf 'child-done\nmain-done')
ref4=$(cd "$T/C" && timeout 60 "$EIG_ABS" prog.eigs 2>&1); ref4_rc=$?
if [ "$ref4" != "$want4" ] || [ "$ref4_rc" -ne 0 ]; then
  echo "FAIL: load_file_shadow (VM did not reach the stdlib from the spliced child's directory: rc=$ref4_rc, got '$(printf '%s' "$ref4" | tr '\n' '|')')"; fail=1
fi
if ! bash build.sh "$T/C/prog.eigs" "$T/prog_c_bin" >"$T/build_c.log" 2>&1; then
  echo "FAIL: load_file_shadow (BUILD FAIL, stdlib-from-child layout)"; tail -3 "$T/build_c.log"; exit 1
fi
got4=$(cd "$T/C" && timeout 60 "$T/prog_c_bin" 2>&1); got4_rc=$?
if [ "$ref4" != "$got4" ] || [ "$ref4_rc" -ne "$got4_rc" ]; then
  echo "FAIL: load_file_shadow (stdlib step from a spliced child: VM rc=$ref4_rc '$(printf '%s' "$ref4" | tr '\n' '|')' AOT rc=$got4_rc '$(printf '%s' "$got4" | tr '\n' '|')' -- the child's literal was resolved from the MAIN directory)"; fail=1
fi
if [ "$fail" -eq 0 ]; then echo "PASS: load_file_shadow (A/B layout from the shadowing cwd + stdlib-from-child layout, 4 assertions)"; fi
exit "$fail"
