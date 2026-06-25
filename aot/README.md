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

The boxed path (slice 1) stays as the fallback for anything not provably
numeric, and the differential harness lets specialization stay aggressive
without risking correctness.

## `num_guard` elision (bounded-induction analysis)

EigenScript has no NaN/Inf, so every arithmetic result routes through
`num_guard`. A sound interval analysis proves which variables stay in a safe
integer range and emits their `+`/`-` updates **raw** (no guard): a loop counter
bounded by a literal condition (`i < B`, `B ≤ 1e7`), and accumulators whose
per-iteration delta is just the counter or a small constant (never compounding
like `s+s`, never a fractional step that inflates the trip count). The bench
loop becomes pure native C:

```c
while (i < 3000000) { s = (s + i); i = (i + 1); }
```

Soundness is verified by the harness (`test/t5_compounding`, `t6_fracstep`):
anything the analysis can't prove falls back to `num_guard` and matches the VM.

**The scalar gain is marginal (~3% on the sum bench)** — gcc had already turned
the NaN check into a well-predicted branch that hides behind the loop's carried
dependency. The elision's real value is as **groundwork for vectorization**.
Measured (hand-written C with the real `num_guard`), guard → guard-free on an
element-wise loop `out[i] = f(in[i])` (no carried dependency):

| loop shape | speedup | why |
|---|---|---|
| scalar sequential (`s+=i`) | ~1.03× | branch hides behind dependency chain |
| element-wise, memory-bound | ~2× | branch removal; SIMD capped by RAM bandwidth |
| element-wise, compute-bound | **~4.2×** | full packed-SIMD (`mulpd`/`vfmadd231pd`) |

`num_guard`'s branches **block auto-vectorization**; the guard-free loop
vectorizes. That ~2–4× lands on element-wise numeric array loops — exactly the
portfolio's hot code (transformer matmuls, physics solvers, neural nets). Two
things capture it, in a later slice: **typed numeric arrays/buffers** (so there
are vectorizable loops to emit) and compiling the *generated* C at
**`-O3 -march=native`** (the current `-O2` doesn't auto-vectorize). Until then
there's nothing vectorizable to compile, so the build stays `-O2`.
