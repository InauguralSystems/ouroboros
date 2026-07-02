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
arithmetic, lists, for-loops; no SIMD-reassociated buffer reductions, no dict
dot-access), so any difference is a real finding:

  * DIVERGENCE  -- both run, outputs differ  (the serious class: a silent
                   miscompile, exactly the bug a differential oracle exists for)
  * AOT_GAP     -- the VM accepts the program but the AOT fails to build it
                   (a feature the AOT should support, or a generator that
                   strayed out of the subset)
  * (VM-invalid programs are skipped -- a generator slip, not an AOT bug.)

Reproducible: every run prints its master seed; re-run with --seed N. Each
finding's program text is saved under --findings-dir for a minimal repro.

IMPORTANT -- point the oracle at the PINNED VM. EIGS must be the SAME
EigenScript the AOT is built and tested against: ouroboros's
.devcontainer/Dockerfile `EIGS_REF` (a tag/branch/SHA; currently v0.19.0), NOT
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
Exit status: nonzero if any DIVERGENCE (or any AOT_GAP under --strict-gaps).
"""
import argparse, os, random, subprocess, sys, tempfile

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

GENERATORS = [gen_precedence, gen_sci_literals, gen_arith, gen_function_returns,
              gen_loop_accum, gen_fstring, gen_soft_keyword_idents, gen_list_ops]


# ---- harness ---------------------------------------------------------------

def run_vm(path):
    p = subprocess.run([EIGS, path], capture_output=True, text=True, timeout=20)
    return p.stdout, (p.returncode == 0 and "Error" not in p.stderr
                      and "Parse error" not in p.stdout)

def run_aot(prog_path, out_path):
    b = subprocess.run(["bash", os.path.join(HERE, "build.sh"), prog_path, out_path],
                       cwd=HERE, capture_output=True, text=True, timeout=120)
    if b.returncode != 0 or not os.path.exists(out_path):
        return None, False, (b.stderr or b.stdout).strip().splitlines()[-1:] or [""]
    rp = subprocess.run([out_path], capture_output=True, text=True, timeout=20)
    return rp.stdout, True, None

def main():
    ap = argparse.ArgumentParser(description="VM<->AOT differential fuzzer")
    ap.add_argument("--count", type=int, default=100)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--findings-dir", default=os.path.join(HERE, "fuzz-findings"))
    ap.add_argument("--strict-gaps", action="store_true",
                    help="fail on AOT build gaps too, not just divergences")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    if not os.path.exists(EIGS):
        sys.exit(f"VM binary not found: {EIGS} (set EIGS=...)")
    print(f"fuzzdiff: seed={seed} count={args.count} VM={EIGS}")
    print("  (oracle must match ouroboros's pinned EIGS_REF; a main checkout "
          "ahead of the pin yields non-actionable main-vs-pin 'drift')")

    divergences, gaps, skipped, tested = [], [], 0, 0
    tmp = tempfile.mkdtemp(prefix="fuzzdiff.")
    prog_path = os.path.join(tmp, "p.eigs")
    out_path = os.path.join(tmp, "p.bin")

    for i in range(args.count):
        gen = rng.choice(GENERATORS)
        prog = gen(rng)
        open(prog_path, "w").write(prog)
        if os.path.exists(out_path):
            os.remove(out_path)
        vm_out, vm_ok = run_vm(prog_path)
        if not vm_ok:
            skipped += 1
            continue
        tested += 1
        aot_out, built, err = run_aot(prog_path, out_path)
        if not built:
            gaps.append((gen.__name__, prog, err))
        elif vm_out != aot_out:
            divergences.append((gen.__name__, prog, vm_out, aot_out))
        if not args.quiet:
            mark = "." if (built and vm_out == aot_out) else ("D" if built else "G")
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
    print(f"  generated {args.count} | VM-valid tested {tested} | skipped(VM-invalid) {skipped}")
    print(f"  DIVERGENCES (both run, outputs differ): {len(divergences)}")
    print(f"  AOT_GAPS    (VM ok, AOT build failed):  {len(gaps)}")
    for name, prog, vm, aot in divergences[:5]:
        print(f"\n  [DIVERGENCE in {name}]")
        for ln in prog.strip().splitlines(): print("    | " + ln)
        print(f"    VM : {vm.strip()!r}")
        print(f"    AOT: {aot.strip()!r}")
    for name, prog, err in gaps[:5]:
        print(f"\n  [AOT_GAP in {name}] {err}")
        for ln in prog.strip().splitlines(): print("    | " + ln)
    save("DIVERGENCE", divergences)
    save("AOT_GAP", [(g[0], g[1]) for g in gaps])
    if divergences or gaps:
        print(f"\n  findings saved to {fd}/ (re-run with --seed {seed})")

    sys.exit(1 if divergences or (args.strict_gaps and gaps) else 0)


if __name__ == "__main__":
    main()
