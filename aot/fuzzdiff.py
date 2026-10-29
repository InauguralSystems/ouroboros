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

Generation stays inside the AOT-supported subset, so any difference is a real
finding. Output is compared byte for byte, with ONE scoped exception:
gen_buffer_reduce prints each association-unspecified reduction (`sum`, `dot`,
`norm` of float buffers, which the AOT reassociates across SIMD lanes) right
after a `~ulp` line, and only those values are compared within ULP_BOUND units
in the last place (never an absolute epsilon); integer-data programs and every
other line stay exact. The classes:

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
excerpt / caret / `at ...` frames dropped, `Error path:N:` blanked; since
#103 death CODES are compared for equality -- the AOT exits cleanly with the
VM's uncaught-error code).

fuzzdiff is a manual tool, not CI. The #100 family (str-compare, recursion
with boxed locals, mixed-compare run-past, builtin returns) is FIXED and
these seeds must stay green; a red here is a new finding.

Open AOT issues a generator can reach are GATED, not skipped silently: each
gate cites its ouroboros issue, and `--known 288,292,...` lets the generators
emit those shapes again (a lane that should be red while the issue is open).
Remove a gate when its issue closes.

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
                        [--only gen_a,gen_b] [--known N,N] [--offline]
Exit status: nonzero if any DIVERGENCE, RUN_PAST or HANG (or any AOT_GAP under
--strict-gaps), if no VM-clean program reaches an AOT comparison, or if more
than 25% of VM-clean programs are AOT gaps.
"""
import argparse, json, math, os, random, re, shutil, struct, subprocess, sys, tempfile
import urllib.error, urllib.request

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

# ---- widening (2026-10): the surfaces the first 17 arms deliberately avoid --
# An HF pilot ran ~30,600 programs of the arms above with 0 findings, and the
# v0.46.0 port's blind critic still found an unfixtured AOT borrow arm (the
# numeric arm of aot_index_borrow_ib, now t412) that no arm could generate.
# Each arm below varies what the AOT's inference BRANCHES on (aot-differential
# invariant 22): result kind, container kind, numeric vs boxed, arity.

# --known N[,N]: let the generators emit shapes of these OPEN ouroboros
# issues (each gate below cites its number); empty by default so the lane
# stays green while the issue is open. Remove a gate when its issue closes.
KNOWN = set()
KNOWN_ISSUES = {288}

def check_known_issues(offline):
    """Refuse expired generator gates; API failures are failures unless opted out."""
    if offline:
        print("fuzzdiff: --offline: NOT verifying gated issue states")
        return
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ouroboros-fuzzdiff"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    for number in sorted(KNOWN_ISSUES):
        url = f"https://api.github.com/repos/InauguralSystems/ouroboros/issues/{number}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as response:
                state = json.load(response).get("state")
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            sys.exit(f"fuzzdiff: cannot verify gated issue #{number}: {exc}; "
                     "use --offline only for an explicit offline run")
        if state != "open":
            sys.exit(f"fuzzdiff: gated issue #{number} is {state or 'unverifiable'}; "
                     "remove its KNOWN gate before fuzzing")
    print(f"fuzzdiff: verified {len(KNOWN_ISSUES)} gated issues are open")

def gen_container_borrow_args(r):
    # Records (dicts) and numbers (and strings) stored in the SAME list/dict
    # slots at different times, then passed as call arguments, compared, and
    # used as field/index bases: the t412 family and its siblings. Slot kinds
    # are tracked so field reads only touch records and arithmetic only
    # numbers (the program stays VM-clean).
    #
    # ouroboros#288 (OPEN): a borrowed element beside a sibling argument whose
    # call MUTATES the same container double-frees or reads a stale value.
    # Off by default so the lane is not permanently red; `--known 288` turns
    # the shape on (the `mut`/`mutr` sibling calls below) and marks each such
    # program `# fuzzdiff: known-#288`, which the report carries. With the
    # flag off the only sibling call is the pure `idn` (invariant 12 allows
    # it) and the generator consumes the same random stream as before.
    use_dict = r.random() < 0.4
    if use_dict:
        keys = ["p", "q", "s"]
        decl = 'C is {"p": 0, "q": 0, "s": 0}'
        dot = r.random() < 0.4
        def slot(i):
            return f"C.{keys[i]}" if dot and r.random() < 0.5 else f'C["{keys[i]}"]'
    else:
        decl = "C is [0, 0, 0]"
        def slot(i):
            return f"C[{i}]"
    lines = ['cpu is {"a": 1, "d": 2}', 'mem is {"a": 5, "d": 6}', decl,
             "define two(a, b) as:", "    return [a, b]",
             "define fst(a, b) as:", "    return a",
             "define three(a, b, c) as:", "    return [c, b, a]",
             "define one(a) as:", "    return a",
             "define kind(a) as:", "    return type of a",
             "define add2(a, b) as:", "    return a + b",
             "define fld(rec) as:", "    return rec.a",
             "define idn(v) as:", "    return v"]
    if 288 in KNOWN:
        lines = ["# fuzzdiff: known-#288"] + lines + [
            "define mut(c, k) as:", "    c[k] is 42", "    return 7",
            "define mutr(c, k) as:", '    c[k] is {"a": 9, "d": 9}', "    return 8"]
    kinds = ["num", "num", "num"]

    def ops(nv, ind):
        # Every block opens by storing EVERY slot, so the tracked kinds hold
        # on each call of run (a second call starts from the first's end).
        out = []
        # Numbers differ PER SLOT (`n + 10*i`, ...): a shared stand-in for two
        # numeric arguments (the pre-t412 aot_index_borrow_ib) prints equal
        # values only if the generator never gives two slots the same number.
        def num(i):
            return r.choice([f"{nv} + {10 * i}", f"{nv} * {i + 2}", f"{i}.5"])
        for i in range(3):
            k = r.choice(["num", "num", "rec"])
            rec = r.choice(["cpu", "mem", f'{{"a": {nv}, "d": {i}}}'])
            out.append(f"{slot(i)} is {num(i) if k == 'num' else rec}")
            kinds[i] = k
        for _ in range(r.randint(5, 9)):
            i, j = r.randrange(3), r.randrange(3)
            op = r.choice(["store", "store", "call2", "call2", "call1", "cmp",
                           "field", "arith", "copy", "local"]
                          + (["m288", "m288"] if 288 in KNOWN else []))
            if op == "store":
                k = r.choice(["num", "num", "rec", "rec", "str"])
                if k == "num":
                    val = num(i)
                elif k == "rec":
                    val = r.choice(["cpu", "mem", f'{{"a": {nv}, "d": 0}}'])
                else:
                    val = f'"s{i}"'
                out.append(f"{slot(i)} is {val}")
                kinds[i] = k
            elif op == "m288":
                m = r.choice(["mut", "mutr"])
                key = f'"{keys[i]}"' if use_dict else str(i)
                out.append(f"print of (two of [{slot(i)}, {m} of [C, {key}]])")
                kinds[i] = "num" if m == "mut" else "rec"
            elif op == "call2":
                f = r.choice(["two", "fst", "three"])
                if f == "three":
                    t = r.randrange(3)
                    third = r.choice([nv, "idn of 4", '"z"', slot(t)])
                    out.append(f"print of (three of [{slot(i)}, {slot(j)}, {third}])")
                else:
                    out.append(f"print of ({f} of [{slot(i)}, {slot(j)}])")
                if kinds[i] == "num" and kinds[j] == "num":
                    out.append(f"print of (add2 of [{slot(i)}, {slot(j)}])")
            elif op == "call1":
                out.append(f"print of ({r.choice(['one', 'kind'])} of {slot(i)})")
            elif op == "cmp":
                rhs = r.choice([slot(j), nv])
                out += [f"if {slot(i)} == {rhs}:", '    print of "eq"', "else:",
                        '    print of "ne"']
            elif op == "field":
                if kinds[i] == "rec":
                    out.append(r.choice([f"print of {slot(i)}.a",
                                         f'print of {slot(i)}["d"]',
                                         f"print of (fld of {slot(i)})",
                                         f"print of (two of [{slot(i)}.a, {slot(j)}])"]))
                else:
                    out.append(f"print of {slot(i)}")
            elif op == "arith":
                if kinds[i] == "num":
                    out.append(f"print of ({slot(i)} + 1)")
                else:
                    out.append(f"print of (kind of {slot(i)})")
            elif op == "copy":
                out.append(f"{slot(i)} is {slot(j)}")
                kinds[i] = kinds[j]
            else:
                pre = "local " if ind else ""
                out += [f"{pre}v is {slot(i)}", "print of (one of v)"]
        return ["    " * ind + ln for ln in out]

    lines.append("define run(n) as:")
    lines += ops("n", 1)
    lines.append("    return 0")
    for _ in range(r.randint(1, 2)):
        lines.append(f"run of {_pint(r)}")
    if r.random() < 0.5:
        lines += ops(str(_pint(r)), 0)
    lines.append("print of C")
    return "\n".join(lines) + "\n"

def gen_closures_values(r):
    # Functions as values: passed, returned, stored in containers, called
    # through a loop variable; closures capturing a for binder, a module name
    # and an enclosing local. The callbacks' RETURN KINDS vary per program
    # (str, null, void, list, dict, bool, number) -- t190's discriminator --
    # with the numeric one sometimes last as the old path's control.
    cbs = {
        "cb_n": ["define cb_n(x) as:", "    return x * 3"],
        "cb_s": ["define cb_s(x) as:", '    return "v" + (str of x)'],
        "cb_l": ["define cb_l(x) as:", "    return [x, x + 1]"],
        "cb_d": ["define cb_d(x) as:", '    return {"v": x}'],
        "cb_z": ["define cb_z(x) as:", "    return null"],
        "cb_p": ["define cb_p(x) as:", "    print of x"],
        "cb_b": ["define cb_b(x) as:", "    return x > 2"],
    }
    names = list(cbs)
    r.shuffle(names)
    pick = names[:r.randint(3, 5)]
    if r.random() < 0.5 and "cb_n" in pick:
        pick.remove("cb_n")
        pick.append("cb_n")
    lines = []
    for nm in pick:
        lines += cbs[nm]
    lines += ["define ap(fn, v) as:", "    return (fn of v)",
              "define ap_local(fn, v) as:", "    local res is fn of v", "    return res"]
    k = _pint(r)
    shapes = ["ap", "table", "dict", "pick", "modcap", "loopcap", "localcap"]
    r.shuffle(shapes)
    for sh in shapes[:r.randint(3, 5)]:
        if sh == "ap":
            for nm in pick:
                f = r.choice(["ap", "ap_local"])
                lines.append(f"print of ({f} of [{nm}, {k}])")
        elif sh == "table":
            lines += [f"fs is [{', '.join(pick)}]", "for g in fs:",
                      f"    print of (g of {k})"]
        elif sh == "dict":
            a, b = pick[0], pick[-1]
            lines += [f'ops is {{"a": {a}, "b": {b}}}', 'h is ops["a"]',
                      f"print of (h of {k})", "h is ops.b", f"print of (h of {k + 1})"]
        elif sh == "pick":
            lines += ["define chooser(c) as:"]
            for idx, nm in enumerate(pick[:-1]):
                lines += [f"    if c == {idx}:", f"        return {nm}"]
            lines += [f"    return {pick[-1]}"]
            for c in r.sample(range(len(pick)), min(3, len(pick))):
                lines += [f"q is chooser of {c}", f"print of (q of {k})"]
        elif sh == "modcap":
            lines += [f"G is {_int(r)}", "define addg(d) as:", "    return d + G",
                      f"print of (ap of [addg, {k}])", f"G is {_int(r)}",
                      f"print of (addg of {k})", f"print of (ap of [addg, {k}])"]
        elif sh == "loopcap":
            vals = [_pint(r) for _ in range(3)]
            lines += ["define mk() as:", "    local acc is []", "    local tot is 0",
                      f"    for x in [{', '.join(map(str, vals))}]:",
                      "        define inner(d) as:", "            return x + d",
                      "        tot is tot + (inner of 10)", "        append of [acc, inner]",
                      "    print of tot", "    return acc"]
            lines += ["for g in (mk of null):", f"    print of (g of {k})"]
        else:
            lines += ["define outer(y) as:", "    local base is y * 2",
                      "    define inner2(d) as:", "        return base + d",
                      f"    return ap of [inner2, {k}]",
                      f"print of (outer of {_pint(r)})"]
    return "\n".join(lines) + "\n"

def gen_bool_values(r):
    # v0.45+ bools: in lists, as dict values, stored into slots that held a
    # number, returned from and passed to compiled functions, `==` with other
    # bools. Sometimes a `bool == number` -- the VM RAISES "cannot compare
    # bool and num with '=='": an error-class case (both must die, same text),
    # or caught, with e.message printed.
    x, a, b = _int(r), _int(r), _int(r)
    # Dict bool fields deliberately use dot reads: ouroboros#294 used to gate
    # these behind index syntax because the field map misclassified them.
    DP, DQ = "D.p", "D.q"
    lines = [f"x is {x}", f"t is x > {a}", f"f is x < {b}",
             "L is [t, f, x == " + str(x) + ", 0]", 'D is {"p": t, "q": f}',
             "define gt(u, w) as:", "    return u > w",
             "define nb(v) as:", "    return not v",
             "define same(u, w) as:", "    return u == w"]
    for _ in range(r.randint(4, 8)):
        op = r.choice(["eq", "eq", "slot", "ret", "arg", "branch", "count", "print"])
        if op == "eq":
            lhs = r.choice(["t", "f", "L[0]", "L[1]", 'D["p"]', DQ, "(gt of [x, 1])"])
            rhs = r.choice(["t", "f", "true", "false", "L[2]", 'D["q"]', "(nb of t)"])
            lines.append(f"print of ({lhs} {r.choice(['==', '!='])} {rhs})")
        elif op == "slot":
            lines += [f"L[3] is {r.choice(['(x > 1)', 'f', 'not t', '(gt of [x, 0])'])}",
                      f"print of (L[3] == {r.choice(['L[0]', 't', 'false'])})"]
        elif op == "ret":
            lines += [f"g is gt of [x, {_int(r)}]", "print of g",
                      f"print of (same of [g, {r.choice(['t', 'f', DP])}])"]
        elif op == "arg":
            lines.append(f"print of (nb of {r.choice(['t', 'L[1]', DQ, '(x == 3)'])})")
        elif op == "branch":
            lines += [f"if {r.choice(['t', 'L[1]', DP, '(t == f)'])}:", '    print of "y"',
                      "else:", '    print of "n"']
        elif op == "count":
            lines += ["c is 0", f"for e in [t, f, true, {DP}]:", "    if e == true:",
                      "        c is c + 1", "print of c"]
        else:
            lines += ["print of L", "print of D", 'D["q"] is not D["q"]', f"print of {DQ}"]
    if r.random() < 0.35:
        bad = f"print of ({r.choice(['t', 'L[0]', DP, '(gt of [x, 2])'])} == {_int(r)})"
        if r.random() < 0.5:
            lines += ["try:", "    " + bad, "catch e:", "    print of e.message",
                      '    print of "caught"']
        else:
            lines += [bad, 'print of "UNREACHABLE"']
    return "\n".join(lines) + "\n"

def gen_try_messages(r):
    # Errors raised at varied sites (index out of range on a list or buffer,
    # operator type errors, a compare error, a field on a number, a division
    # or modulo by zero, a user `throw` of a str or dict), caught, with the
    # message PRINTED: under `catch` the error text is program data, so every
    # AOT raise site must carry the VM's kind and text byte for byte.
    k, w = _pint(r), _word(r)
    sites = [
        ("rt", ["xs is [1, 2, 3]", f"print of xs[{k + 2}]"]),
        ("rt", ["bb is buffer of 3", f"print of bb[{k + 2}]"]),
        ("rt", ["ys is [1]", f"ys[{k}] is 2"]),
        # Variable and literal non-number operands pin the runtime-error
        # lowering (#259): neither may stop in the emitter.
        ("rt", [f'sv is "{w}"', f"print of (sv - {k})"]),
        ("rt", [f"lv is [{k}]", "print of (lv - 1)"]),
        ("rt", [f'print of ("{w}" - {k})']),
        ("rt", [f"print of ([{k}] - 1)"]),
        ("rt", [f"print of (len of {k})"]),
        ("rt", [f"v is {k}", "print of v.f"]),
        ("rt", [f'print of ({k} < "{w}")']),
        ("rt", [f"print of ((x > 1) == {k})"]),
        ("rt", [f"print of ({k} / (x - x))"]),
        ("rt", [f"print of ({k} % 0)"]),
        ("throw", [f'throw of "bad {w} {k}"']),
        ("tdict", [f'throw of {{"code": {k}, "why": "{w}"}}']),
    ]
    def handler(kind, ind, name):
        p = "    " * ind
        if kind == "rt":
            return [f"{p}print of {name}.kind", f"{p}print of {name}.message",
                    f"{p}m is {name}.message", f"{p}print of (len of m)"]
        if kind == "throw":
            return [f"{p}print of {name}"]
        return [f"{p}print of {name}.code", f"{p}print of {name}.why"]
    lines = ["x is 2"]
    for n in range(r.randint(2, 4)):
        kind, body = r.choice(sites)
        # Per-site names: two sites drawing `bb` put a module buffer and a
        # `local bb` in one program, a refused AOT shape (19 of 19 gaps on
        # the first HF aot lane, 2026-10-13). No RNG draw, so seeds stand.
        body = [re.sub(r"\b(xs|bb|ys|sv|lv|v)\b", rf"\g<1>{n}", b) for b in body]
        where = r.choice(["module", "fn", "loop", "nested"])
        if where == "module":
            lines += ["try:"] + ["    " + b for b in body] + ['    print of "not reached"',
                      f"catch e{n}:"] + handler(kind, 1, f"e{n}")
        elif where == "fn":
            # `local`: a body write of a name another site binds at module
            # scope is the by-name refused outward-write order shape
            loc = [re.sub(r"^(\w+) is ", r"local \1 is ", b) for b in body]
            lines += [f"define risky{n}(d) as:"] + ["    " + b for b in loc] + [
                "    return d",
                "try:", f"    print of (risky{n} of {k})", f"catch e{n}:"] + handler(kind, 1, f"e{n}")
        elif where == "loop":
            lines += ["for i in range of 4:", "    try:", f"        if i == {k % 4}:"] + \
                     ["            " + b for b in body] + ["        print of i",
                     f"    catch e{n}:"] + handler(kind, 2, f"e{n}")
        else:
            lines += ["try:", "    try:"] + ["        " + b for b in body] + [
                f"    catch in{n}:"] + handler(kind, 2, f"in{n}") + [
                f'        throw of "rethrown {n}"', f"catch e{n}:", f"    print of e{n}"]
        lines.append(f'print of "after {n}"')
    return "\n".join(lines) + "\n"

def gen_buffer_elementwise(r):
    # Buffer element reads/writes and ELEMENTWISE arithmetic loops -- the
    # shapes the AOT's buffer/SIMD recognizers (vec_loop, outvec, the
    # stencil path) take. Every operation is per-element (no reduction), so
    # the result is association-free and the default byte-exact comparison
    # stands; no tolerance is introduced. Loop forms, offsets, in-place
    # writes, buffer parameters and both bound spellings vary.
    n = r.randint(3, 12)
    c0, c1, c2 = _int(r), r.choice(["1", "2", "0.5", "3"]), _pint(r)
    loop_kind = r.choice(["while", "for"])
    def loop(var, bound, body, ind=0):
        p = "    " * ind
        if loop_kind == "for":
            return [f"{p}for {var} in range of {bound}:"] + [p + "    " + b for b in body]
        return [f"{p}{var} is 0", f"{p}loop while {var} < {bound}:"] + \
               [p + "    " + b for b in body] + [f"{p}    {var} is {var} + 1"]
    lines = [f"n is {n}", "a is buffer of n", "b is buffer of n", "c is buffer of n"]
    lines += loop("i", "n", [f"a[i] is i * {c1} + {c0}", f"b[i] is (n - i) * {c2}"])
    k = _pint(r)
    exprs = [f"a[i] + b[i]", f"a[i] - b[i]", f"a[i] * b[i]", f"a[i] / b[i]",
             f"a[i] * {k}", f"{k} - a[i]", f"(a[i] - b[i]) * {k}", f"a[i] * b[i] + {k}",
             f"b[i] / {k}"]
    for _ in range(r.randint(1, 3)):
        sh = r.choice(["ew", "ew", "stencil", "inplace", "fn", "two"])
        if sh == "ew":
            lines += loop("i", "n", [f"c[i] is {r.choice(exprs)}"])
        elif sh == "stencil":
            lines += ["m is n - 1"] + loop("i", "m", ["c[i] is a[i + 1] - a[i]"])
        elif sh == "inplace":
            lines += loop("i", "n", [f"a[i] is a[i] {r.choice(['*', '+', '-'])} {k}"])
        elif sh == "two":
            lines += loop("i", "n", [f"c[i] is {r.choice(exprs)}",
                                     f"b[i] is c[i] {r.choice(['+', '*'])} {_pint(r)}"])
        else:
            # defined once: a second `define scale` is a by-name refusal
            if "define scale(out, src, s) as:" not in lines:
                lines += ["define scale(out, src, s) as:"] + \
                         loop("j", "(len of out)", ["out[j] is src[j] * s"], 1) + \
                         ["    return 0"]
            lines += [f"scale of [c, {r.choice(['a', 'b'])}, {k}]"]
    lines += loop("q", "n", ["print of c[q]"])
    if r.random() < 0.5:
        lines += loop("q", "n", ["print of (a[q] + b[q])"])
    lines.append("print of (len of c)")
    return "\n".join(lines) + "\n"

ULP_MARK = "~ulp"   # gen_buffer_reduce prints this line before each reassociable value

def gen_buffer_reduce(r):
    # `sum`/`dot`/`norm` over buffers (whole and ranged): the builtins whose
    # float association is UNSPECIFIED, which the AOT reassociates across SIMD
    # lanes. Each such value is printed right after a `~ulp` line, and ONLY
    # those lines are compared within ULP_BOUND units in the last place (never
    # an absolute epsilon); every other line, and every value of an
    # INTEGER-data program, stays byte-exact. Data are nonnegative, so a
    # reassociated sum carries no cancellation and its error is bounded in
    # ULPs of the result. A scalar `loop while` accumulation has a defined
    # left-to-right order and is printed WITHOUT the mark: exact.
    n = r.randint(5, 300)
    floaty = r.random() < 0.7
    if floaty:
        ca, da = r.choice(["0.013", "0.1", "0.37", "1.5"]), r.choice(["0.4", "1.7", "0.05"])
        cb, db = r.choice(["0.027", "0.3", "2.5"]), r.choice(["0.4", "0.01", "3.3"])
    else:
        ca, da, cb, db = str(_pint(r)), str(_pint(r)), str(_pint(r)), str(_int(r) + 10)
    lo = r.randint(0, n // 2)
    hi = r.randint(lo + 1, n)
    lines = [f"n is {n}", "a is buffer of n", "b is buffer of n", "i is 0",
             "loop while i < n:", f"    a[i] is i * {ca} + {da}",
             f"    b[i] is i * {cb} + {db}", "    i is i + 1"]
    reds = [f"sum of a", f"dot of [a, b]", f"norm of b", f"sum of a[{lo}:{hi}]",
            f"dot of [a[{lo}:{hi}], b[{lo}:{hi}]]", f"norm of a[{lo}:{hi}]"]
    r.shuffle(reds)
    for e in reds[:r.randint(2, 5)]:
        # The mark only on float data: integer data stay below 2^53
        # (|a|,|b| <= ~2700, n <= 300, dot <= ~2.2e9), so every
        # association gives the same bits and the compare must be exact.
        if floaty:
            lines += [f'print of "{ULP_MARK}"']
        lines += [f"print of ({e})"]
    if r.random() < 0.5:
        lines += ["s is 0", "j is 0", "loop while j < n:", "    s is s + a[j]",
                  "    j is j + 1", "print of s"]
    lines += [f"print of a[{lo}]", f"print of b[{hi - 1}]"]
    return "\n".join(lines) + "\n"

def gen_spawn_join(r):
    # OS-thread concurrency inside the AOT envelope: `spawn of [fn, args...]`
    # over UNOBSERVED compiled workers whose results vary in kind, joined in
    # spawn order, and channels. Output is deterministic by construction:
    # joined values print in join order, and values that two producers
    # interleave on one channel are reduced to a CONSERVED total, never
    # printed in arrival order. (`task_spawn` is refused by name by
    # compile.eigs -- no native scheduler, ouroboros#188 -- and an observed
    # function anywhere refuses `spawn`, so neither is generated.)
    ws = {
        "w_n": ["define w_n(k) as:", "    local acc is 0", "    for i in range of 50:",
                "        acc is acc + (i + k)", "    return acc"],
        "w_s": ["define w_s(k) as:", '    return "w" + (str of k)'],
        "w_l": ["define w_l(k) as:", "    return [k, k * 2]"],
        "w_d": ["define w_d(k) as:", '    return {"k": k}'],
        "w_z": ["define w_z(k) as:", "    return null"],
    }
    names = list(ws)
    r.shuffle(names)
    pick = names[:r.randint(2, 4)]
    lines = []
    for nm in pick:
        lines += ws[nm]
    nthr = r.randint(2, 5)
    lines += ["hs is []", f"for k in range of {nthr}:"]
    lines += [f"    append of [hs, spawn of [{pick[0]}, k]]"]
    if len(pick) > 1:
        lines += [f"h2 is spawn of [{pick[1]}, {_pint(r)}]"]
    lines += ["for h in hs:", "    print of (thread_join of h)"]
    if len(pick) > 1:
        lines += ["print of (thread_join of h2)"]
    if r.random() < 0.7:
        cnt, nprod = r.randint(2, 6), r.randint(1, 3)
        lines += ["define prod(c, m, base) as:", "    for i in range of m:",
                  "        send of [c, base + i * 3]", "    return m",
                  f"ch is channel of {r.randint(1, 8)}", "ps is []",
                  f"for p in range of {nprod}:",
                  f"    append of [ps, spawn of [prod, ch, {cnt}, p * 100]]",
                  "tot is 0", f"for i in range of {cnt * nprod}:"]
        if nprod == 1:
            lines += ["    v is recv of ch", "    print of v", "    tot is tot + v"]
        else:
            lines += ["    tot is tot + (recv of ch)"]
        lines += ["print of tot", "for p in ps:", "    print of (thread_join of p)"]
    return "\n".join(lines) + "\n"

GENERATORS = [gen_precedence, gen_sci_literals, gen_arith, gen_function_returns,
              gen_loop_accum, gen_fstring, gen_soft_keyword_idents, gen_list_ops,
              gen_str_compare, gen_dict_ops, gen_loop_while, gen_recursion_str,
              gen_null_death,
              gen_outward_write, gen_type_demotion, gen_local_marker, gen_observer_reads,
              # appended LAST so `--only <the 17 above>` replays every old seed
              gen_container_borrow_args, gen_closures_values, gen_bool_values,
              gen_try_messages, gen_buffer_elementwise, gen_buffer_reduce,
              gen_spawn_join]


# ---- harness ---------------------------------------------------------------

TIMEOUT_RC = 124   # a hung side is a named finding, never a harness crash

def _run(cmd, **kw):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=20, **kw)
        return p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        return out, "<TIMEOUT after 20s>", TIMEOUT_RC

def run_vm(path):
    return _run([EIGS, path])

def run_aot(prog_path, out_path):
    b = subprocess.run(["bash", os.path.join(HERE, "build.sh"), prog_path, out_path],
                       cwd=HERE, capture_output=True, text=True, timeout=120)
    if b.returncode != 0 or not os.path.exists(out_path):
        # Surface the THROW message, not the last stack frame: the transpiler
        # dies with the message followed by excerpt/caret/`at ...` lines, and
        # the old [-1:] reported "  at <module> (<path>:4222)" for every gap.
        # Filter with the same discipline as norm_err, keep the first
        # substantive line (an Error/AOT: line when present).
        txt = norm_err((b.stderr or "") + "\n" + (b.stdout or ""))
        lines = [ln for ln in txt.splitlines() if ln.strip()]
        msg = next((ln for ln in lines
                    if "rror" in ln or ln.lstrip().startswith("AOT:")),
                   lines[0] if lines else "")
        return None, None, None, [msg]
    o, e, rc = _run([out_path])
    return o, e, rc, None

def prebuild_runtime(tmp):
    """Build the cached runtime outside the per-program 120 second budget."""
    source = os.path.join(tmp, "prebuild.eigs")
    output = os.path.join(tmp, "prebuild.bin")
    with open(source, "w") as f:
        f.write('print of "fuzzdiff prebuild"\n')
    print("fuzzdiff: building libeigsrt.a once before timed program builds")
    result = subprocess.run(["bash", os.path.join(HERE, "build.sh"), source, output],
                            cwd=HERE)
    if result.returncode != 0:
        sys.exit(f"fuzzdiff: runtime prebuild failed (rc={result.returncode})")

# Measured 2026-10-13 (v0.46.0 pin, -march=native). Dev box (no AVX2): seeds
# 201/202, 529 reassociated values, largest VM/AOT distance 14 ulp. HF lane
# aot-20261013T1006Z (AMD EPYC 7R13, AVX2): 61,200 programs, largest 31 ulp,
# and all 50 lane divergences were reductions at 17..31 ulp (#295). The
# bound is the next power of two above the AVX2 maximum. A plant that drops
# or duplicates ONE lane moves a 5..300-term sum by ~1e14 ulp.
ULP_BOUND = 32

def _ulp_key(x):
    i = struct.unpack("<q", struct.pack("<d", x))[0]
    return i if i >= 0 else -(i & 0x7FFFFFFFFFFFFFFF)

def ulp_match(vm, aot, stats):
    """gen_buffer_reduce's comparator: byte-exact on every line EXCEPT the one
    after a `~ulp` line, which must be a finite number on both sides within
    ULP_BOUND units in the last place. Records the largest distance seen in
    stats["max_ulp"] and the count compared in stats["ulp_n"]."""
    v, a = vm.split("\n"), aot.split("\n")
    if len(v) != len(a):
        return False
    for k, (x, y) in enumerate(zip(v, a)):
        if k == 0 or v[k - 1] != ULP_MARK or x == y:
            if x != y:
                return False
            if k and v[k - 1] == ULP_MARK:
                stats["ulp_n"] += 1
            continue
        try:
            fx, fy = float(x), float(y)
        except ValueError:
            return False
        if not (math.isfinite(fx) and math.isfinite(fy)):
            return False
        d = abs(_ulp_key(fx) - _ulp_key(fy))
        stats["ulp_n"] += 1
        stats["max_ulp"] = max(stats["max_ulp"], d)
        if d > ULP_BOUND:
            return False
    return True

def norm_err(s):
    # The aot/test/run.sh _err normalization, verbatim: drop the VM-only source
    # excerpt / caret / stack-frame lines, blank the line number the AOT can't
    # site. Everything else -- error kind, message text, ordering -- must match.
    out = []
    for ln in s.splitlines():
        if re.match(r'^\s+[0-9]+ \|', ln) or re.match(r'^\s+\|.*\^', ln) \
           or ln.startswith("  at "):
            continue
        out.append(re.sub(r'^Error .*:[0-9]+:', 'Error <file>:', ln))
    return "\n".join(out)

def main():
    global EIGS
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
    ap.add_argument("--known", default="",
                    help="comma-separated OPEN ouroboros issue numbers whose shapes the "
                         "generators may emit (288: borrowed element beside a sibling call "
                         "that mutates the container); off by "
                         "default so the lane stays green")
    ap.add_argument("--offline", action="store_true",
                    help="explicitly skip the gated-issue GitHub API check")
    args = ap.parse_args()
    check_known_issues(args.offline)
    requested_known = {int(x) for x in args.known.split(",") if x}
    # Fixed issues remain accepted as no-ops for their documented repro
    # commands, but are no longer generator gates or API-polled known issues.
    retired_fixed = {292, 293}
    unknown = requested_known - KNOWN_ISSUES - retired_fixed
    if unknown:
        sys.exit(f"fuzzdiff: no generator gate for issue(s): {sorted(unknown)}")
    KNOWN.update(requested_known & KNOWN_ISSUES)
    gens = GENERATORS
    if args.only:
        wanted = set(args.only.split(","))
        gens = [g for g in GENERATORS if g.__name__ in wanted]
        missing = wanted - {g.__name__ for g in gens}
        if missing:
            sys.exit(f"fuzzdiff: unknown generator(s): {', '.join(sorted(missing))}")

    seed = args.seed if args.seed is not None else random.randrange(1 << 30)
    rng = random.Random(seed)
    # A bare command name resolves through PATH, as in every other harness
    # (`EIGS=eigenscript bash test/run.sh`); CI's devcontainer runs it that way.
    if os.sep not in EIGS:
        EIGS = shutil.which(EIGS) or EIGS
    if not os.path.exists(EIGS):
        sys.exit(f"VM binary not found: {EIGS} (set EIGS=...)")
    print(f"fuzzdiff: seed={seed} count={args.count} VM={EIGS}" + (f" only={args.only}" if args.only else ""))
    print("  (oracle must match ouroboros's pinned EIGS_REF; a main checkout "
          "ahead of the pin yields non-actionable main-vs-pin 'drift')")

    divergences, run_pasts, gaps, hangs = [], [], [], []
    tested, compared, err_tested, err_rejected, err_both_die = 0, 0, 0, 0, 0
    ulp_stats = {"ulp_n": 0, "max_ulp": 0}
    tmp = tempfile.mkdtemp(prefix="fuzzdiff.")
    prebuild_runtime(tmp)
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
        if vm_rc == TIMEOUT_RC:
            # Generators emit terminating programs, so a VM hang is a
            # generator or VM bug, never an error-class match (a deadlock
            # on both sides used to count as "both-die matched").
            hangs.append((gen.__name__, prog, "VM"))
            mark = "H"
        elif vm_rc != 0:
            # Error-class case (#101 item 4): the VM rejects/raises. The AOT
            # must refuse to build it (loud build throw = pass) or die too,
            # with byte-exact pre-death stdout and _err-normalized stderr.
            err_tested += 1
            aot_out, aot_err, aot_rc, _berr = run_aot(prog_path, out_path)
            if aot_out is None:
                err_rejected += 1          # loud build-time reject: both sides refuse
            elif aot_rc == TIMEOUT_RC:
                hangs.append((gen.__name__, prog, "AOT"))
                mark = "H"
            else:
                compared += 1
                if aot_rc == 0:
                    run_pasts.append((gen.__name__, prog, vm_out, vm_err, aot_out))
                    mark = "R"
                elif (vm_out != aot_out or norm_err(vm_err) != norm_err(aot_err)
                      or aot_rc != vm_rc):
                    divergences.append((gen.__name__, prog,
                                        vm_out + vm_err + f"<rc={vm_rc}>",
                                        aot_out + aot_err + f"<rc={aot_rc}>"))
                    mark = "D"
                else:
                    err_both_die += 1  # stdout + normalized error + code match
        else:
            tested += 1
            aot_out, aot_err, aot_rc, err = run_aot(prog_path, out_path)
            if aot_out is None:
                gaps.append((gen.__name__, prog, err))
                mark = "G"
            elif aot_rc == TIMEOUT_RC:
                hangs.append((gen.__name__, prog, "AOT"))
                mark = "H"
            else:
                compared += 1
                if aot_rc != 0 or not (
                        ulp_match(vm_out, aot_out, ulp_stats) if gen is gen_buffer_reduce
                        else vm_out == aot_out):
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
    print(f"  generated {args.count} | VM-clean tested {tested} | compared {compared} | "
          f"error-class tested {err_tested} "
          f"(both-reject {err_rejected}, both-die matched {err_both_die})")
    if ulp_stats["ulp_n"]:
        print(f"  ULP-compared reduction values: {ulp_stats['ulp_n']}, largest distance "
              f"{ulp_stats['max_ulp']} ulp (bound {ULP_BOUND})")
    print(f"  DIVERGENCES (outputs/deaths differ):    {len(divergences)}")
    print(f"  RUN_PASTS   (VM rejects, AOT exits 0):  {len(run_pasts)}")
    print(f"  AOT_GAPS    (VM ok, AOT build failed):  {len(gaps)}")
    print(f"  HANGS       (a side ran past 20s):      {len(hangs)}")
    def tag(prog):
        return " (known-#288 shape)" if "# fuzzdiff: known-#288" in prog else ""
    for name, prog, vm, aot in divergences[:5]:
        print(f"\n  [DIVERGENCE in {name}{tag(prog)}]")
        for ln in prog.strip().splitlines(): print("    | " + ln)
        print(f"    VM : {vm.strip()!r}")
        print(f"    AOT: {aot.strip()!r}")
    for name, prog, vm_out, vm_err, aot_out in run_pasts[:5]:
        print(f"\n  [RUN_PAST in {name}{tag(prog)}] VM dies, AOT exits 0")
        for ln in prog.strip().splitlines(): print("    | " + ln)
        print(f"    VM : {(vm_out + vm_err).strip()!r}")
        print(f"    AOT: {aot_out.strip()!r}")
    for name, prog, err in gaps[:5]:
        print(f"\n  [AOT_GAP in {name}] {err}")
        for ln in prog.strip().splitlines(): print("    | " + ln)
    save("DIVERGENCE", divergences)
    save("RUN_PAST", run_pasts)
    for name, prog, side in hangs[:5]:
        print(f"\n  [HANG in {name}] {side} ran past 20s")
        for ln in prog.strip().splitlines(): print("    | " + ln)
    save("AOT_GAP", [(g[0], g[1]) for g in gaps])
    save("HANG", hangs)
    if divergences or run_pasts or gaps or hangs:
        print(f"\n  findings saved to {fd}/ (re-run with --seed {seed})")

    too_many_gaps = tested > 0 and len(gaps) / tested > 0.25
    vacuous = compared == 0
    if vacuous:
        print("\n  GATE FAILED: zero programs reached a VM<->AOT comparison")
    if too_many_gaps:
        print(f"\n  GATE FAILED: {len(gaps)}/{tested} VM-clean programs were AOT gaps "
              "(more than 25%; allows isolated unsupported shapes but catches a broadly "
              "broken runtime or emitter)")
    sys.exit(1 if divergences or run_pasts or hangs or vacuous or too_many_gaps
             or (args.strict_gaps and gaps) else 0)


if __name__ == "__main__":
    main()
