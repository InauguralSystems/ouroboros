# ouroboros

A self-hosting compiler for EigenScript, **written in EigenScript** — source to
the C VM's bytecode, run on the same engine (and JIT) the C compiler's output
uses. The language with no outside compiler: it consumes itself.

This is the most self-referential of the ecosystem's stress projects, and a fit
for EigenScript's founding question (an observer with no outside): a language
reproducing its own toolchain has no outside compiler to lean on.

## How it closes the loop

```
source ─► eigen_tokenize ─► eigen_parse ─► cg_* (codegen) ─► [code, constants]
                                                                   │
                                              vm_run_bytecode ◄─────┘
                                                   │
                                              the C VM (+ JIT)
```

- **Front-end** (`src/frontend.eigs`) — tokenizer + parser, vendored from the
  stdlib meta-circular interpreter `lib/eigen.eigs`. Source → AST.
- **Back-end** (`src/codegen.eigs`) — the new contribution: walks the AST and
  emits the C VM's exact bytecode (opcodes + little-endian 16-bit operands) as
  plain EigenScript data — a list of byte ints plus a constant pool.
- **Bridge** — `vm_run_bytecode` (EigenScript builtin, PR #251) assembles a chunk
  from that data and runs it on `vm_execute`.

Because the emitted chunk runs on the *real* VM, the back-end only has to emit
*semantically correct* bytecode — not byte-identical to `compile_ast`. The oracle
is therefore behavioral.

## Oracle

`test/run.sh`: for each program, compile+run it with ouroboros and run the same
source through the C `eigenscript` directly; the two stdouts must be
byte-identical. Behavioral parity on the real VM.

## Status

A working compiler for: literals, identifiers, `name is expr` assignment,
arithmetic (`+ - * / %`), comparisons (`== != < > <= >=`), unary (`-` / `not`),
calls, list literals, **control flow** (`if`/`elif`/`else`, `loop while`,
`for`-in, `break`/`continue`, short-circuit `and`/`or`), and **functions** —
`define`, parameters, `return`, slot-allocated locals, recursion, and the
`f of [a,b]` arg-spread calling convention; **dicts, indexing, dot access,
comprehensions**; and the **observer opcodes** — `OBSERVE_ASSIGN` on every `is`,
the six `PREDICATE`s, and the `LOOP_STALL_CHECK`/`LOOP_CAP_CHECK` classifier;
**interrogatives + temporal** (`what`/`who`/`when`/… `is x [at line]`, `prev of
x`), **bitwise + compound assignment**, **destructuring**, **parameter
defaults**, and **hex literals**. All 43 programs in `test/programs/` are at
byte-identical parity with the C evaluator (plus 14 `reject_one` cases and the
byte-exact bootstrap fixed point; incl. factorial, fibonacci, and firing
observer predicates over full windows).

### Roadmap

- [x] expressions, assignment, calls, lists (slice 1)
- [x] control flow: `if`/`elif`/`else`, `loop while`, `for`, `break`/`continue`,
      short-circuit `and`/`or` (slice 2 — forward/backward jumps, back-patching)
- [x] functions: `define`, params, `return`, slot-allocated locals, `OP_CLOSURE`,
      `f of [..]` arg-spread, recursion, module-var read/mutate (slice 3 — nested
      chunk descriptors; scope-aware `SET_LOCAL` vs outward `SET_NAME`)
- [x] dicts, indexing (get/set), dot access (get/set), list comprehensions with
      filters (slice 4 — the oracle caught a calling-convention bug in ouroboros's
      own codegen, F-OURO-9: single-element list args must not spread)
- [x] observer opcodes (slice 5): `OBSERVE_ASSIGN`/`OBSERVE_ASSIGN_LOCAL` on every
      `is`, `PREDICATE` (all six kinds), and the `LOOP_STALL_CHECK` vs
      `LOOP_CAP_CHECK` classifier (#247). Interrogatives/temporal out of scope
      (front-end grammar mismatch with C).
- [x] **full bootstrap fixed point**: ouroboros self-compiles BOTH its front-end
      and codegen, and the fully self-hosted compiler reproduces the bytecode of
      its front-end, its codegen, and a test program byte-for-byte
      (`test/bootstrap.eigs`). Took `try`/`catch` (codegen) and `local`
      (front-end). The language reproduces its entire toolchain.
- [x] interrogatives + temporal (slice 6): `<kw> is x [at <line>]`, `prev of x` —
      C-matching grammar + `INTERROGATE`/`INTERROGATE_NAMED`/`_AT` codegen,
      `OP_LINE` emission, and the `record_history` primitive for runtime history.
      All forms byte-for-byte with the C evaluator.

## Native (AOT) compiler

The repo holds a *second* compiler: `aot/compile.eigs` lowers EigenScript AST to
C (linked against `aot/aot_rt.h`) for native execution, instead of to bytecode.
Same rule — the bytecode VM is the byte-exact oracle: every `aot/test/tN_*.eigs`
program is diffed byte-for-byte against `eigenscript PROG.eigs`, and CI runs both
suites. See `aot/README.md` for the emitter, the differential harness, and the
performance arc.

## Layout

    src/frontend.eigs   tokenizer + parser (vendored from lib/eigen.eigs)
    src/codegen.eigs    AST -> bytecode (the self-hosted back-end)
    aot/                native AOT compiler (AST -> C); see aot/README.md
    ouroboros.eigs      CLI: compile + run a source file
    test/programs/      sample programs
    test/run.sh         behavioral-parity oracle vs the C evaluator
    eigs                symlink to the EigenScript binary
    FINDINGS.md         language findings surfaced by the port

## Running

    ./eigs ouroboros.eigs path/to/program.eigs    # compile + run
    ./test/run.sh                                  # parity suite

Private until EigenScript clears the GitHub Linguist threshold.
