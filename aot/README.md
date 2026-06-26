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
2. **numeric functions DONE** — `define f(p…) as: … return e` compiles to native
   `double f(double…)` with forward prototypes (recursion/mutual recursion),
   params + locals as C doubles, calls `f(args)` in numeric context. `fib(32)`:
   ~37× over the VM. (Boxed/buffer params are a later extension.)
3. **typed numeric buffers DONE** (checked path) — `buffer of N` / `zeros of N`
   become C `Value*`; element read/write via `aot_buf_get`/`aot_buf_set` (integer
   check + negative-index resolve + bounds), `len of buf` via `aot_buf_len`.
   Parity-verified (`test/t7_buffer`). The *vectorized* path is blocked on a
   semantic decision — see "Vectorization vs no-NaN/Inf" below. Lists/dicts/for
   next.
4. observer ops (`observer_slot_update*` are callable — the differentiator comes along)
5. **type specialization** — numeric-scalar spike **DONE** (see Speed below, ~64×).

## Vectorization vs no-NaN/Inf (a forge-the-language finding)

The big remaining multiple is SIMD vectorization of element-wise numeric array
loops (`out[i] = f(in[i])`) — the shape of transformer matmuls, physics solvers,
neural nets. But `num_guard` (which enforces EigenScript's no-NaN/Inf guarantee
on every numeric op) **blocks auto-vectorization**, and this holds across every
*sound* formulation tested (gcc `-O3 -march=native`, compute-bound element-wise
map, real semantics):

| guard | vs raw | vectorized? |
|---|---|---|
| branchy `if(x!=x)…` | ~3.4× slower | no |
| branchless ternary | ~3.7× slower | no (gcc re-introduces branches) |
| clamp-only `fmin/fmax` | ~8.8× slower | no (IEEE NaN semantics block `minsd`) |
| **raw (no guard)** | **1.0× (baseline)** | **yes — packed SIMD** |

Only fully-raw vectorizes — and raw is **unsound** (admits NaN/Inf, diverging
from the VM). `-ffast-math` would let the clamp vectorize, but it *is* dropping
the no-NaN/Inf guarantee. So the vectorization win looked like it required
*relaxing* no-NaN/Inf.

**Resolved — it doesn't.** The guarantee is *observable behavior* (the compiler
"as-if" rule), not a per-op branch. `num_guard`'s effect can be delivered as a
branch-free **packed** guard (`cmp x,x` + `and` for NaN→0, `min`/`max` clamp)
that vectorizes and is byte-exact vs scalar `num_guard` (verified incl. NaN/Inf).
Spike (`bench/simd_guard.c`): the sound packed guard runs **3.67× over
scalar-guard on cloud AVX2** (1.37× on this box's 2-wide SSE2), with the
bounded-range analysis eliding guards toward the raw ceiling (~11× on cloud).
So no-NaN/Inf stays universal and the language still vectorizes — niche by
identity, general by implementation. Full write-up: **`DESIGN_no_nan_simd.md`**.

**Implemented.** The AOT recognizes the element-wise buffer-map pattern
(`loop while ctr < bound: buf[ctr] is <+/-/* over buf[ctr] reads & literals>;
ctr is ctr+1`) and emits a SIMD loop + scalar tail behind a runtime guard
(`ctr==0 && bound<=len`), falling back to the checked scalar loop otherwise — so
it's always correct. The packed guard (`aot_vguard`) and a portable SIMD layer
(`AOT_VW`/`aot_v*`, SSE2/AVX2/scalar per `-march`) live in `aot_rt.h`; builds use
`-O3 -march=native`. Parity-verified (`test/t8_vecmap`); **3.66× over the VM** on
a 2M-element compute-heavy map (this box, SSE2 2-wide; AVX2 is wider).

**Range-elision toward the raw ceiling.** Buffer *value*-bound inference
(`infer_buffer_bounds`: a buffer starts at 0, each `buf[?] is expr` fill raises
its bound by `expr`'s bound; null = unknown). If a map's expression provably
can't overflow (every intermediate `< 1e300 ≪ 1e308` given the input buffers'
bounds), `num_guard` is identity there, so the packed guard is **elided** and the
map emits raw vector arithmetic. Sound (parity-verified) and only fires when
proven. Measured: **5.18× over the per-op-guarded path** on a compute-bound map
(this box, SSE2; AVX2 wider) — the move from the guarded floor toward the raw
ceiling. `t8_vecmap` exercises the elided path (bounded fill); `t7_buffer`/`buf1`
keep guards (unbounded fill).

**Scalar broadcasts.** A loop-invariant numeric scalar in a map expression is
broadcast across lanes (`aot_vset(k)`) — the loop body only assigns the counter +
buffer elements, so any other scalar is invariant. Scalar *value*-bounds are
tracked too (a scalar assigned only literals → bounded by `max |literal|`), so
the realistic kernel `out[i] = in[i]*scale + bias` with constant coefficients
elides the guard and emits raw vector arithmetic (`test/t9_broadcast`).

**Iota (`i`-as-value).** The counter used as a value (e.g. `out[i] = i*dt`,
`out[i] = in[i] + i`) emits an index vector `aot_viota(_vi)` = `{_vi, _vi+1, …}`
(correct because the SIMD path only runs when `ctr==0`, so lane `k` holds index
`_vi+k`). The counter isn't a literal-bounded scalar, so these maps stay guarded
(sound). `test/t10_iota`.

**Division.** `/` vectorizes via `aot_vdiv` (packed `div`, then mask the `b==0`
lanes to `0`, then guard) — matching the VM's value semantics (`b==0 → 0`,
overflow clamped). It's never elided (no lower bound on the divisor), so div maps
stay guarded. `test/t11_div`. *Known gap:* the VM prints a `division by zero`
warning to **stderr**; the AOT doesn't reproduce it (no source-line tracking in
generated C — pre-existing in the scalar path), so div-by-zero programs match on
stdout/value but not on the stderr warning.

## Numeric functions (slice 2)

`define f(p1, …) as: … return e` compiles to a native `static double f(double …)`.
All params + body-assigned locals are C doubles; the body reuses the scalar
specialization (arithmetic, `if`/`else`, `loop while`, `return`). Forward
prototypes are emitted for all functions first, so recursion and mutual recursion
work. A call `f of (x)` / `f of [a, b]` in numeric context emits `f(x)` / `f(a, b)`
(use `f of (x)`, not `f of [x]`, for one arg — the 1-element list doesn't spread).
`test/t12_func`; `fib(32)` runs **~37× over the VM** (native recursion). Functions
are self-contained numeric (no module-global reads, no buffer/string params yet).

## Neighbor indexing / stencils

A map may read `buf[i ± c]` (constant offset), e.g. a difference `out[i] =
in[i] - in[i-1]` or a blur. Reads off the ends would wrap (negative) or error
(positive OOB) in the VM, so the vectorized loop is **split**: the safe interior
`[-min_off, n - max_off)` vectorizes raw with offset pointers (`_p_in + _vi - 1`),
while the head/tail boundaries use the **checked** path (`aot_buf_get`, which
resolves negatives and bounds-errors exactly like the VM). `test/t13_stencil`.
*Limits:* the write index must be `i` (offset 0); a full-range loop with a `+c`
read errors at the tail like the VM, and a safe `i < n-1` bound isn't recognized
yet (the bound must be an ident/num) — so positive-offset stencils need padded
buffers for now. Next: binop loop bounds; boxed/buffer function params.

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
