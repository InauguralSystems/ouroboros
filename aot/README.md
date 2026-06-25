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
5. **type specialization** — numeric-scalar spike **DONE** (see Speed below, ~64×);
   broaden to typed lists/buffers and elide `num_guard` on integer-range vars next

## Speed: the specialization spike landed (~64×)

Numeric-scalar specialization is implemented (greatest-fixpoint inference:
assume numeric, demote on evidence). A variable proven to always hold a number
becomes a native C `double` — no `Value`, no env, no refcount; numbers are boxed
only at boundaries (calls, non-numeric contexts).

Measured on a 3M-iteration counting loop (`s += i`), this dev box, JIT-on VM:

| | wall (3M) | iterations/sec |
|---|---|---|
| VM (JIT) | ~1.00s | ~3.1M |
| AOT boxed (slice 1) | ~1.30s | ~2.3M |
| **AOT specialized** | **~0.022s** | **~213M** |

**~64× per-iteration** over the JIT'd VM (~45× on total wall incl. startup) —
right on the ~68× native gap. Output is byte-identical to the VM (the oracle
held). The win is **real native iteration**, verified two ways:

- It **scales linearly** with N (3M→30M: loop time 0.015s→0.141s, 9.4× for 10×
  iterations) — a closed-formed loop would stay flat.
- The **disassembly is a real loop**: `addsd` (native double add) + `ucomisd/jp`
  (`num_guard`'s inlined NaN check) per op.

The specialized loop:

```c
double i = 0, s = 0;
while (i < 3000000) { s = num_guard(s + i); i = num_guard(i + 1); }
print(make_num(s));   /* box only at the boundary */
```

Remaining per-iteration cost is `num_guard`'s NaN/inf check; eliding it on
provably integer-range induction vars would close most of the rest — a later
optimization. The boxed path (slice 1) stays as the fallback for anything not
provably numeric, and the differential harness lets specialization stay
aggressive without risking correctness.
