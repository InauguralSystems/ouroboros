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

Slice 1 — a working compiler for: number/string/null literals, identifiers,
`name is expr` assignment, arithmetic (`+ - * / %`), comparisons
(`== != < > <= >=`), unary (`-` / `not`), calls (`f of arg`), and list literals.
6/6 programs at byte-identical parity with the C evaluator.

### Roadmap

- [x] expressions, assignment, calls, lists (slice 1)
- [ ] control flow: `if` / `loop` / `for` (forward + backward jumps, offsets)
- [ ] functions: `define`, locals (`GET_LOCAL`/`SET_LOCAL` slot allocation), `OP_CLOSURE`
- [ ] dicts, indexing, dot access, comprehensions
- [ ] the signature opcodes: `OBSERVE_ASSIGN`, `INTERROGATE`, `PREDICATE`, and
      the `LOOP_STALL_CHECK` vs `LOOP_CAP_CHECK` classifier (#247)
- [ ] the bootstrap fixed point: ouroboros compiles its own source

## Layout

    src/frontend.eigs   tokenizer + parser (vendored from lib/eigen.eigs)
    src/codegen.eigs    AST -> bytecode (the self-hosted back-end)
    ouroboros.eigs      CLI: compile + run a source file
    test/programs/      sample programs
    test/run.sh         behavioral-parity oracle vs the C evaluator
    eigs                symlink to the EigenScript binary
    FINDINGS.md         language findings surfaced by the port

## Running

    ./eigs ouroboros.eigs path/to/program.eigs    # compile + run
    ./test/run.sh                                  # parity suite

Private until EigenScript clears the GitHub Linguist threshold.
