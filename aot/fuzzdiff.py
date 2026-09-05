#!/usr/bin/env python3
"""
VM <-> AOT differential fuzzer.

The AOT compiler's whole contract is byte-exactness with the bytecode VM (the
oracle). The hand-written aot/test/*.eigs corpus only covers a few dozen shapes;
this generates many programs that exercise the edge classes where AOT bugs have
actually hidden -- `of` precedence vs trailing infix, scientific/leading-dot
numeric literals, mixed numeric/list returns, f-string interpolation, loop
accumulators, soft-keyword identifiers -- and asserts:

    eigenscript prog.eigs   ==   (aot-compiled prog).run()    byte for byte.

Generation stays inside the AOT-supported, association-stable subset (scalar
arithmetic, lists, dict indexing, for/while loops, recursion; no
SIMD-reassociated buffer reductions), so any difference is a real finding:

  * DIVERGENCE  -- both run, outputs differ  (the serious class: a silent
                   miscompile, exactly the bug a differential oracle exists for)
  * RUN_PAST    -- the VM REJECTS the program (nonzero exit) but the AOT binary
                   exits 0: the AOT ran past an error the oracle stops at --
                   the silent-wrong-answer class (ouroboros#96/#100 item 1).
  * AOT_GAP     -- the VM accepts the program but the AOT fails to build it
                   (a feature the AOT should support, or a generator that
                   strayed out of the subset)

A program the VM rejects is a TEST CASE, not a skip (#101 item 4 -- the old
skip made this class structurally unfindable): the AOT must either refuse to
build it (a loud build-time reject is a pass) or die too, with byte-exact
pre-death stdout and the same error kind/message on stderr under the SAME
normalization contract as aot/test/run.sh's _err class (VM-only source
excerpt / caret / `at ...` frames dropped, `Error line N:` blanked; since
#103 death CODES are compared for equality -- the AOT exits cleanly with the
VM's uncaught-error code).

fuzzdiff is a manual tool, not CI. The #100 family (str-compare, recursion
with boxed locals, mixed-compare run-past, builtin returns) is FIXED and
these seeds must stay green; a red here is a new finding.

Reproducible: every run prints its master seed; re-run with --seed N. Each
finding's program text is saved under --findings-dir for a minimal repro.

IMPORTANT -- point the oracle at the PINNED VM. EIGS must be the SAME
EigenScript the AOT is built and tested against: ouroboros's
.devcontainer/Dockerfile `EIGS_REF` (a tag/branch/SHA), NOT
your local main checkout. The default below is the sibling working tree for dev
convenience, but a main checkout that is AHEAD of the pin will flag main-vs-pin
deltas (a language feature in main but not yet in the pinned VM) as false
"drift" -- not actionable until EIGS_REF moves. For CI-relevant results, install
the pinned ref and run with EIGS=$(command -v eigenscript), or build the pinned
tag and point EIGS at that binary. (This bit once: the soft-keyword feature on
EigenScript main flagged a "drift" the v0.19.0-pinned AOT shouldn't chase.)

Usage:
    EIGS=<pinned-vm> python3 fuzzdiff.py [--count N] [--seed N]
                        [--findings-dir DIR] [--strict-gaps] [--quiet]
Exit status: nonzero if any DIVERGENCE or RUN_PAST (or any AOT_GAP under
--strict-gaps).
"""
import argparse, os, random, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
# Default oracle = the sibling working tree (dev convenience). For CI-relevant
# results set EIGS to the PINNED VM (.devcontainer Dockerfile EIGS_REF) instead.
EIGS = os.environ.get("EIGS", os.path.join(HERE, "..", "..", "EigenScript", "src", "eigenscript"))


# ---- generators ------------------------------------------------------------
# Each takes an RNG and returns a complete program that prints a deterministic
# sequence of values. They MUST stay inside the AOT byte-exact subset.

def _int(r):    return r.randint(-9, 9)
def _pint(r):   return r.randint(1, 9)            # positive (loop bounds, divisors)
def _flit(r):   return r.choice(["1e2", "1e-2", ".5e2", "2.5e3", "1E2", "3.5",
                                 "0.25", "10.0", "7.0"])
def _addop(r):  return r.choice(["+", "-"])
def _mulop(r):  return r.choice(["*", "%"])       # skip '/' (div formatting noise)

def gen_precedence(r):
    # `of` binds unary-or-tighter; must not absorb trailing infix.
    x, k = _pint(r), _pint(r)
    xs = [_pint(r) for _ in range(r.randint(2, 4))]
    lst = "[" + ", ".join(map(str, xs)) + "]"
    lines = [
        f"x is {x}",
        f"xs is {lst}",
        f"print of (sqrt of x {_addop(r)} {k})",
        f"print of (len of xs {_addop(r)} {k})",
        f"print of ({k} + len of xs)",
        f"print of (abs of (0 - {x}) {_mulop(r)} {_pint(r)})",
        "print of (sqrt of sqrt of 16.0)",
    ]
    return "\n".join(lines) + "\n"

def gen_sci_literals(r):
    a, b = _flit(r), _flit(r)
    return (f"print of {a}\n"
            f"print of ({a} {_addop(r)} {b})\n"
            f"print of ({_flit(r)} {_mulop(r)} {_pint(r)})\n")

def _expr(r, depth=2):
    if depth <= 0 or r.random() < 0.3:
        return str(_int(r)) if r.random() < 0.6 else _flit(r)
    op = r.choice(["+", "-", "*"])
    return f"({_expr(r, depth-1)} {op} {_expr(r, depth-1)})"

def gen_arith(r):
    return "\n".join(f"print of ({_expr(r)})" for _ in range(r.randint(2, 4))) + "\n"

def gen_function_returns(r):
    # Mixed numeric/list returns -> exercises func_ret_type's all-returns rule.
    # Printing the call result directly works for both a number and a list.
    body_num = f"return n {_addop(r)} {_pint(r)}"
    other = r.choice([f"return [n, n {_addop(r)} {_pint(r)}]", f"return n {_mulop(r)} {_pint(r)}"])
    lines = [
        "define f(n) as:",
        f"    if n > 0:",
        f"        {body_num}",
        f"    {other}",
        f"print of (f of {_pint(r)})",
        f"print of (f of (0 - {_pint(r)}))",
        f"print of (f of 0)",
    ]
    return "\n".join(lines) + "\n"

def gen_loop_accum(r):
    n = r.randint(2, 6)
    init = _int(r)
    step = r.choice([f"i", f"i {_addop(r)} {_pint(r)}", f"i {_mulop(r)} {_pint(r)}"])
    wrap = r.random() < 0.5
    body = [f"total is {init}",
            f"for i in range of {n}:",
            f"    total is total + ({step})"]
    if wrap:
        body = ["unobserved:"] + ["    " + b for b in body]
    body.append(f"print of total")
    return "\n".join(body) + "\n"

def gen_fstring(r):
    a, b = _pint(r), _pint(r)
    interps = [f"{{a {_addop(r)} {b}}}", "{sqrt of a}", "{a}",
               f"{{len of [a, b, {_pint(r)}]}}"]
    r.shuffle(interps)
    return (f"a is {a}\n"
            f"b is {b}\n"
            f'print of f"p {interps[0]} q {interps[1]} r {interps[2]}"\n')

def gen_soft_keyword_idents(r):
    # prev/at are identifier-like outside their special forms; `what` binds as a
    # loop var / param (just not before bare `is`). Compound `at +=` is fine
    # since #55 (the parser desugars it to a plain assign).
    return (f"at is {_int(r)}\n"
            f"at += {_pint(r)}\n"
            f"prev is {_int(r)}\n"
            f"for what in [{_pint(r)}, {_pint(r)}, {_pint(r)}]:\n"
            f"    at is at + what\n"
            f"print of at\n"
            f"print of prev\n")

def gen_list_ops(r):
    xs = [_int(r) for _ in range(r.randint(2, 5))]
    lst = "[" + ", ".join(map(str, xs)) + "]"
    i = r.randint(0, len(xs) - 1)
    return (f"xs is {lst}\n"
            f"print of (len of xs)\n"
            f"print of xs[{i}]\n"
            f"print of (xs[{i}] {_addop(r)} {_pint(r)})\n")

def _word(r):
    return r.choice(["apple", "banana", "cherry", "kiwi", "mango", "pear"])

def gen_str_compare(r):
    # Strings as DATA, not just concat: ordering/equality (#100 item 1 --
    # AOT_CMP silently answers 0 for any non-num/num pair). The occasional
    # mixed num/str compare is an error-class case: the VM RAISES, so the AOT
    # must die too (#100's run-past probe).
    a, b = _word(r), _word(r)
    cmps = ["<", ">", "<=", ">=", "==", "!="]
    lines = [f'a is "{a}"', f'b is "{b}"']
    for _ in range(r.randint(2, 4)):
        lines.append(f"print of (a {r.choice(cmps)} b)")
    lines.append(f'print of ((a + "x") {r.choice(cmps)} a)')
    if r.random() < 0.3:
        lines.append(f"print of ({_pint(r)} < a)")
    return "\n".join(lines) + "\n"

def gen_dict_ops(r):
    # Dict get / set / iteration through [key] indexing (dot-access on module
    # dicts stays out -- t60's class). Several of these shapes are currently
    # loud AOT build throws ("emit_num on non-numeric node str" -- e.g.
    # re-assigning a literal key, and some set-then-iterate combinations),
    # i.e. AOT_GAPs; they stay generated so the envelope hole stays visible.
    ks = ["a", "b", "c", "d"][:r.randint(2, 4)]
    items = ", ".join(f'"{k}": {_int(r)}' for k in ks)
    lines = [f"d is {{{items}}}",
             f'print of d["{r.choice(ks)}"]',
             f'd["z"] is d["{ks[0]}"] {_addop(r)} {_pint(r)}',
             f'print of d["z"]']
    if r.random() < 0.4:
        lines.append(f'd["{ks[-1]}"] is {_int(r)}')
    keys = "[" + ", ".join(f'"{k}"' for k in ks) + "]"
    lines += [f"for k in {keys}:", "    print of d[k]"]
    return "\n".join(lines) + "\n"

def gen_loop_while(r):
    # `loop while` with a numeric accumulator, sometimes a string one.
    n = r.randint(2, 7)
    lines = ["i is 0", f"acc is {_int(r)}"]
    if r.random() < 0.5:
        lines += ['s is ""',
                  f"loop while i < {n}:",
                  f"    acc is acc + (i {_mulop(r)} {_pint(r)})",
                  "    s is s + (str of i)",
                  "    i is i + 1",
                  "print of acc",
                  "print of s"]
    else:
        lines += [f"loop while i < {n}:",
                  f"    acc is acc + (i {_addop(r)} {_pint(r)})",
                  "    i is i + 1",
                  "print of acc"]
    return "\n".join(lines) + "\n"

def gen_recursion_str(r):
    # Recursion with a NON-NUMERIC local (#100 items 2/3): boxed fn locals
    # live in the one global env, so inner frames clobber outer ones, and the
    # string local goes through silent aot_num.
    depth = r.randint(2, 4)
    ret = r.choice(["tag", "tag", "r2"])   # mostly the outer frame's local
    return ("define f(n) as:\n"
            f'    tag is "{_word(r)[0]}" + (str of n)\n'
            "    if n <= 0:\n"
            "        return tag\n"
            "    r2 is f of (n - 1)\n"
            f"    return {ret}\n"
            f"print of (f of {depth})\n"
            "print of (f of 0)\n")

def gen_null_death(r):
    # The t60 shape: print real work, then die on a null field/index access.
    # BOTH sides must die -- since #103 the AOT dies by a clean exit with the
    # VM's own code (1) -- with byte-exact pre-death stdout, the same
    # normalized error, and the same death code. This is the generator that
    # exercises the error-class BOTH-DIE comparison path (the other
    # error-class shapes all build-reject or run past).
    lines = [f"x is {_pint(r)}",
             f"print of (x {_addop(r)} {_pint(r)})"]
    if r.random() < 0.3:
        # Pre-death stdout carrying a string compare (#100 item 1 -- was
        # expected-red until the AOT_CMP fix; now byte-exact).
        lines.append(f'print of ("{_word(r)}" < "{_word(r)}")')
    lines += ["n is null",
              r.choice(['print of n.f', 'print of n["k"]']),
              'print of "UNREACHABLE — the VM stops above"']
    return "\n".join(lines) + "\n"

# ---- #160: the strata the first 13 generators never emitted ----------------
# Blind-critic rounds 35/49/55 found silent-wrong miscompiles in shapes no
# generator produced (outward writes from function bodies, a module name that
# changes type class, `local`-marked vs unmarked body assigns, observer reads).
# Each arm below converts one manual stratum rotation into a standing
# instrument. All are VM-clean (rc 0) and deterministic.

def gen_outward_write(r):
    # (a) a function body RMW-writes a MODULE global (outward-mutable scope),
    # called several times; optionally the global is then DEMOTED to a string
    # (the round-55 shape: VM 3 / AOT 2, both rc 0).
    g = r.choice(["total", "count", "acc"])
    init = _int(r)
    step = _pint(r)
    calls = r.randint(1, 3)
    lines = [f"{g} is {init}",
             "define bump(k) as:",
             f"    {g} is {g} + k",
             f"    return {g}"]
    for _ in range(calls):
        lines.append(f"print of (bump of {step})")
    lines.append(f"print of {g}")
    if r.random() < 0.5:
        lines += [f'{g} is "{_word(r)}"', f"print of {g}"]
    if r.random() < 0.5:
        # a second writer: a plain (non-RMW) outward assignment
        lines += ["define reset() as:", f"    {g} is {_int(r)}", "    return 0",
                  "print of (reset of [])", f"print of {g}"]
    return "\n".join(lines) + "\n"

def gen_type_demotion(r):
    # (b) one module name bound to two or three type classes in sequence, read
    # between each rebinding, including from inside a function.
    n = r.choice(["v", "slot", "cur"])
    shapes = [f"{_int(r)}", f'"{_word(r)}"', f"[{_pint(r)}, {_pint(r)}]",
              f"{_flit(r)}", "null"]
    r.shuffle(shapes)
    lines = ["define show() as:", f"    return {n}"]
    for sh in shapes[:r.randint(2, 3)]:
        lines += [f"{n} is {sh}", f"print of {n}", "print of (show of [])"]
    return "\n".join(lines) + "\n"

def gen_local_marker(r):
    # (c) a function assigns a name that is ALSO a module name: `local` marks a
    # fresh binding (module value untouched), the unmarked form writes the
    # module binding. Both forms, the module value printed after each call.
    n = r.choice(["x", "level", "n2"])
    lines = [f"{n} is {_int(r)}",
             "define shadow() as:",
             f"    local {n} is {_pint(r)}",
             f"    return {n} + 1",
             "define write() as:",
             f"    {n} is {_pint(r)}",
             f"    return {n} + 1",
             "print of (shadow of [])", f"print of {n}",
             "print of (write of [])", f"print of {n}"]
    if r.random() < 0.5:
        lines += ["define both() as:",
                  f"    local {n} is {_int(r)}",
                  f"    {n} is {n} + {_pint(r)}",
                  f"    return {n}",
                  "print of (both of [])", f"print of {n}"]
    return "\n".join(lines) + "\n"

def gen_observer_reads(r):
    # (d) observer forms on a named binding: `report of x`, `<predicate> of x`,
    # `prev of x` after a deterministic assignment history (flat, ramp, or
    # decaying), at module scope and from inside a function.
    n = r.choice(["x", "y", "temp"])
    kind = r.choice(["flat", "ramp", "decay"])
    steps = r.randint(3, 7)
    lines = [f"{n} is {_pint(r)}.0"]
    for _ in range(steps):
        if kind == "flat":
            lines.append(f"{n} is {n}")
        elif kind == "ramp":
            lines.append(f"{n} is {n} + {_pint(r)}")
        else:
            lines.append(f"{n} is {n} * 0.5")
    pred = r.choice(["converged", "stable", "improving", "oscillating", "diverging", "equilibrium"])
    lines += [f"print of (report of {n})", f"print of ({pred} of {n})"]
    if r.random() < 0.5:
        lines.append(f"print of (prev of {n})")
    if r.random() < 0.5:
        lines += ["define peek() as:", f"    return report of {n}", "print of (peek of [])"]
    return "\n".join(lines) + "\n"

GENERATORS = [gen_precedence, gen_sci_literals, gen_arith, gen_function_returns,
              gen_loop_accum, gen_fstring, gen_soft_keyword_idents, gen_list_ops,
              gen_str_compare, gen_dict_ops, gen_loop_while, gen_recursion_str,
              gen_null_death,
              gen_outward_write, gen_type_demotion, gen_local_marker, gen_observer_reads]


# ---- harness ---------------------------------------------------------------

def run_vm(path):
    p = subprocess.run([EIGS, path], capture_output=True, text=True, timeout=20)
    return p.stdout, p.stderr, p.returncode

def run_aot(prog_path, out_path):
    b = subprocess.run(["bash", os.path.join(HERE, "build.sh"), prog_path, out_path],
                       cwd=HERE, capture_output=True, text=True, timeout=120)
    if b.returncode != 0 or not os.path.exists(out_path):
        # Surface the THROW message, not the last stack frame: the transpiler
        # dies with the message followed by excerpt/caret/`at ...` lines, and
        # the old [-1:] reported "  at <module> (line 4222)" for every gap.
        # Filter with the same discipline as norm_err, keep the first
        # substantive line (an Error/AOT: line when present).
        txt = norm_err((b.stderr or "") + "\n" + (b.stdout or ""))
        lines = [ln for ln in txt.splitlines() if ln.strip()]
        msg = next((ln for ln in lines
                    if "rror" in ln or ln.lstrip().startswith("AOT:")),
                   lines[0] if lines else "")
        return None, None, None, [msg]
    rp = subprocess.run([out_path], capture_output=True, text=True, timeout=20)
    return rp.stdout, rp.stderr, rp.returncode, None

def norm_err(s):
    # The aot/test/run.sh _err normalization, verbatim: drop the VM-only source
    # excerpt / caret / stack-frame lines, blank the line number the AOT can't
    # site. Everything else -- error kind, message text, ordering -- must match.
    out = []
    for ln in s.splitlines():
        if re.match(r'^\s+[0-9]+ \|', ln) or re.match(r'^\s+\|.*\^', ln) \
           or ln.startswith("  at "):
            continue
        out.append(re.sub(r'^Error line [0-9]+:', 'Error line:', ln))
    return "\n".join(out)

def main():
    ap = argparse.ArgumentParser(description="VM<->AOT differential fuzzer")
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--findings-dir", default=os.path.join(HERE, "fuzz-findings"))
    ap.add_argument("--strict-gaps", action="store_true",
                    help="fail on AOT build gaps too, not just divergences")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--only", default=None,
                    help="comma-separated generator names (e.g. gen_outward_write,"
                         "gen_observer_reads): run one stratum alone instead of the "
                         "uniform mix, so a new arm gets its own seeds (#160)")
    args = ap.parse_args()
    gens = GENERATORS
    if args.only:
        wanted = set(args.only.split(","))
        gens = [g for g in GENERATORS if g.__name__ in wanted]
        missing = wanted - {g.__name__ for g in gens}
        if missing:
            sys.exit(f"fuzzdiff: unknown generator(s): {', '.join(sorted(missing))}")

    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    if not os.path.exists(EIGS):
        sys.exit(f"VM binary not found: {EIGS} (set EIGS=...)")
    print(f"fuzzdiff: seed={seed} count={args.count} VM={EIGS}" + (f" only={args.only}" if args.only else ""))
    print("  (oracle must match ouroboros's pinned EIGS_REF; a main checkout "
          "ahead of the pin yields non-actionable main-vs-pin 'drift')")

    divergences, run_pasts, gaps = [], [], []
    tested, err_tested, err_rejected, err_both_die = 0, 0, 0, 0
    tmp = tempfile.mkdtemp(prefix="fuzzdiff.")
    prog_path = os.path.join(tmp, "p.eigs")
    out_path = os.path.join(tmp, "p.bin")

    for i in range(args.count):
        gen = rng.choice(gens)
        prog = gen(rng)
        open(prog_path, "w").write(prog)
        if os.path.exists(out_path):
            os.remove(out_path)
        vm_out, vm_err, vm_rc = run_vm(prog_path)
        mark = "."
        if vm_rc != 0:
            # Error-class case (#101 item 4): the VM rejects/raises. The AOT
            # must refuse to build it (loud build throw = pass) or die too,
            # with byte-exact pre-death stdout and _err-normalized stderr.
            err_tested += 1
            aot_out, aot_err, aot_rc, _berr = run_aot(prog_path, out_path)
            if aot_out is None:
                err_rejected += 1          # loud build-time reject: both sides refuse
            elif aot_rc == 0:
                run_pasts.append((gen.__name__, prog, vm_out, vm_err, aot_out))
                mark = "R"
            elif (vm_out != aot_out or norm_err(vm_err) != norm_err(aot_err)
                  or aot_rc != vm_rc):
                divergences.append((gen.__name__, prog,
                                    vm_out + vm_err + f"<rc={vm_rc}>",
                                    aot_out + aot_err + f"<rc={aot_rc}>"))
                mark = "D"
            else:
                err_both_die += 1  # both died: stdout + normalized error + code match
        else:
            tested += 1
            aot_out, aot_err, aot_rc, err = run_aot(prog_path, out_path)
            if aot_out is None:
                gaps.append((gen.__name__, prog, err))
                mark = "G"
            elif vm_out != aot_out or aot_rc != 0:
                divergences.append((gen.__name__, prog, vm_out,
                                    aot_out if aot_rc == 0
                                    else aot_out + f"<AOT died rc={aot_rc}> " + aot_err))
                mark = "D"
        if not args.quiet:
            sys.stdout.write(mark); sys.stdout.flush()
    if not args.quiet:
        print()

    # report + save
    fd = args.findings_dir
    def save(kind, items):
        if not items:
            return
        os.makedirs(fd, exist_ok=True)
        for n, item in enumerate(items):
            with open(os.path.join(fd, f"{kind}_{seed}_{n}.eigs"), "w") as f:
                f.write(item[1])

    print(f"\n=== fuzzdiff results (seed {seed}) ===")
    print(f"  generated {args.count} | VM-clean tested {tested} | "
          f"error-class tested {err_tested} "
          f"(both-reject {err_rejected}, both-die matched {err_both_die})")
    print(f"  DIVERGENCES (outputs/deaths differ):    {len(divergences)}")
    print(f"  RUN_PASTS   (VM rejects, AOT exits 0):  {len(run_pasts)}")
    print(f"  AOT_GAPS    (VM ok, AOT build failed):  {len(gaps)}")
    for name, prog, vm, aot in divergences[:5]:
        print(f"\n  [DIVERGENCE in {name}]")
        for ln in prog.strip().splitlines(): print("    | " + ln)
        print(f"    VM : {vm.strip()!r}")
        print(f"    AOT: {aot.strip()!r}")
    for name, prog, vm_out, vm_err, aot_out in run_pasts[:5]:
        print(f"\n  [RUN_PAST in {name}] VM dies, AOT exits 0")
        for ln in prog.strip().splitlines(): print("    | " + ln)
        print(f"    VM : {(vm_out + vm_err).strip()!r}")
        print(f"    AOT: {aot_out.strip()!r}")
    for name, prog, err in gaps[:5]:
        print(f"\n  [AOT_GAP in {name}] {err}")
        for ln in prog.strip().splitlines(): print("    | " + ln)
    save("DIVERGENCE", divergences)
    save("RUN_PAST", run_pasts)
    save("AOT_GAP", [(g[0], g[1]) for g in gaps])
    if divergences or run_pasts or gaps:
        print(f"\n  findings saved to {fd}/ (re-run with --seed {seed})")

    sys.exit(1 if divergences or run_pasts or (args.strict_gaps and gaps) else 0)


if __name__ == "__main__":
    main()
