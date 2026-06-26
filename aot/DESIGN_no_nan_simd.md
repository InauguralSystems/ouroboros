# Design: no-NaN/Inf and SIMD (how to stay niche *and* general)

## The tension

EigenScript guarantees programs never observe NaN or Infinity — `num_guard`
runs after every numeric op (`x!=x → 0`, `|x|>1e308 → ±1e308`). This is **not
decorative**: the observer system (`converged`/`stable`/`equilibrium`) compares
`dH`/entropy against thresholds, and NaN destroys ordering (`NaN != NaN`, every
NaN comparison is false). One NaN would silently corrupt convergence detection —
the language's core feature. So the guarantee is load-bearing for the niche.

But `num_guard`'s **branch** blocks SIMD auto-vectorization of element-wise
numeric loops (`out[i] = f(in[i])`) — the hot shape of matmuls, physics solvers,
neural nets. That's exactly the code that makes the language *general*.

## The wrong answer

An opt-in "native-float" (raw IEEE) type for hot arrays. It fragments the
language ("every number is safe" becomes "some numbers", a footgun next to the
observer), and `-ffast-math` is the same relaxation in disguise. It forces a
choice: niche identity **or** general speed.

Measured — every *sound* guard formulation fails to vectorize (gcc
`-O3 -march=native`, compute-bound element-wise map):

| guard | vs raw | vectorized? |
|---|---|---|
| branchy `if(x!=x)…` | ~3.4× slower | no |
| branchless ternary | ~3.7× slower | no (gcc re-adds branches) |
| clamp-only `fmin/fmax` | ~8.8× slower | no (IEEE NaN blocks `minsd`) |
| raw (unsound) | 1.0× | yes, packed SIMD |

So at the *scalar* level, sound = slow. The fix is not at the scalar level.

## The right answer: observable guarantee + packed guard + elision

The guarantee is **observable behavior** (the compiler "as-if" rule), not a
per-op implementation. The promise is: *no program reads/compares/prints/observes
a NaN/Inf.* It is **not**: *every `+` is individually clamped.* So:

1. **Keep the semantic universal** — identity and observer safety intact. No
   second type, no unsafe mode.
2. **Implement `num_guard`'s effect as a branch-free PACKED guard** that
   vectorizes: `cmp x,x` + `and` (NaN→0), then `min`/`max` clamp. Verified
   semantically identical to scalar `num_guard`, including NaN and overflow
   (`bench/simd_guard.c`).
3. **Elide guards where intermediates provably can't overflow** (the
   bounded-range analysis already in `compile.eigs`) → those loops emit fully
   raw → full SIMD width.

Magnitude is then a spectrum from *packed-guard floor* (guard every op) to *raw
ceiling* (elide where safe), and both multipliers favor the cloud AOT target.

## Spike results (`bench/simd_guard.c`)

Element-wise map, per-op guarding (the conservative floor — guard *every* op):

| host | SIMD | packed-guard vs scalar-guard | raw(unsound) ceiling |
|---|---|---|---|
| dev box (N3350 Goldmont) | SSE2, 2-wide | **1.37×** | — |
| cloud (EPYC 7763) | AVX2, 4-wide | **3.67×** | 11.15× |

`NaN/Inf semantics match num_guard: 1` on both — the packed guard *is*
`num_guard`. AVX-512 (8-wide) would widen it further. The gap to the 11× raw
ceiling is guard overhead, which elision closes.

## Decision

**Keep no-NaN/Inf universal; reimplement it vectorizably.** Niche by identity,
general by implementation — the optimizer reconciles them, not the programmer.

## Implementation plan

1. Reframe `num_guard`'s contract as the observable guarantee; enumerate
   observation points (observer reads, comparisons, prints, read-back stores).
2. AOT element-wise numeric loops emit a **portable packed guard** (GCC/Clang
   vector extensions or per-`-march` intrinsics; auto-scales SSE2→AVX2→AVX-512).
3. Extend the bounded-range analysis to array element values where provable →
   elide the guard → raw, full-width SIMD.
4. Parity holds: the packed guard is byte-exact vs `num_guard`, so the VM stays
   the differential oracle unchanged.
