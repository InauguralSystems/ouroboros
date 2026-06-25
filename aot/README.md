# EigenScript AOT (ahead-of-time native compiler)

Transpiles EigenScript to C that is compiled and linked against the **existing**
EigenScript runtime, producing a native binary. The bytecode VM stays as the
**differential oracle**: AOT output must match `eigenscript prog.eigs`
byte-for-byte.

## Architecture

- **Front-end reuse** — tokenize/parse come from ouroboros's `src/frontend.eigs`
  (vendored from EigenScript's `lib/eigen.eigs`). AOT adds only a backend.
- **Emitter** — `compile.eigs` walks the AST emitting C (one case per node kind,
  mirroring `eigen_eval`'s semantics and ouroboros's `cg_node` structure).
- **Support layer** — `aot_rt.h`: a thin wrapper over the runtime with one rule —
  **operands are consumed, result is owned** (mirrors the stack VM's
  pop-operands/push-result), so generated C is leak-clean by linear value flow.
- **Link** — `build.sh` links the generated C against the runtime SOURCES minus
  `main.c` (the same set `make embed-smoke`/`lsp` use); generated C supplies its
  own `main()`.

## Usage

```bash
bash build.sh program.eigs out_binary   # transpile + compile + link
bash test/run.sh                        # differential harness: native == VM
```

Override the runtime checkout with `EIGS_DIR=` (default `../../EigenScript`).

## Slices

1. **DONE** — literals, arithmetic + comparison, module-level `x is expr`,
   single-arg named calls (`print of ...`), `if/else`, `loop while`.
   Parity-verified vs the VM (`test/run.sh`).
2. functions / calls / recursion (locals as C-scoped, the call convention)
3. lists / dicts / `for` / closures
4. observer ops (`observer_slot_update*` are callable — the differentiator comes along)
5. **type specialization** — where the speed is

## Speed: correctness first, specialization second

Slice 1 is deliberately correctness-first and is **not yet faster** than the VM.
Measured on a 3M-iteration counting loop (this dev box, JIT-on VM):

| | wall |
|---|---|
| VM (JIT) | ~1.00s |
| AOT slice 1 | ~1.30s |

It's slower because slice 1 still **boxes every number** (`make_num` + refcount
per op) and **resolves every variable by name** (`env_get`/`env_set` hash lookup)
— so it trades cheap, JIT'd bytecode dispatch for `aot_*` call overhead. The
native win is gated on **type specialization** (slice 5): infer numeric locals
and emit unboxed C `double`s with slot/C-local binding, turning the loop into a
raw C `for` with zero allocation. The measurement above pinpoints the two costs
to eliminate — it's where the ~68× headroom actually lives.
