# Flat-buffer tensor type — design scope

## Why

Tensors today are **nested lists** of boxed `Value` numbers. Every `matmul` /
`add` / `relu` call **flattens** the nested list to a `double[]`, computes, and
**rebuilds** a nested list. F-OURO-15 (see `../FINDINGS.md`) showed that flatten
is the bottleneck for real neural code — ~110M boxed reads per 4000 forwards of
Tidepool's policy — and **both the VM and the AOT pay it**. As a result the AOT's
verbatim tensor-builtin path is only ~1.1× over the VM, not the ~90× that
flat-buffer kernels reach.

A tensor backed by a **contiguous flat `double[]` + shape** removes the flatten
on both sides at once:

- **VM**: the tensor builtins read/write flat memory directly — the flatten tax
  disappears, so the VM itself gets faster.
- **AOT**: `AotTensor` becomes a **zero-copy view** over the buffer's `data` +
  shape (no `aot_tensor_from_value` copy), and the existing output-axis SIMD +
  `num_guard` elision machinery — which already targets `VAL_BUFFER` — applies in
  place. This is where the ~90× comes from, with no rewrite to explicit loops.

## Decision 1 — representation: extend `VAL_BUFFER` (NOT a new `VAL_TENSOR`)

`VAL_BUFFER` is already a flat `double[]` (`struct { double *data; int count; }`)
and the AOT already treats it as a flat native array with int-indexing,
output-axis SIMD, and guard elision — **all the matmul work targets buffers**.
So:

```c
struct { double *data; int count; int rows, cols; } buffer;
//  rows == 0  =>  unshaped 1-D (back-compat default; count is the length)
//  rows  > 0  =>  2-D, with rows*cols == count
```

- Fits the existing Value union (dominated by `list`/`dict`/`text_builder`) — **no
  Value-size growth**.
- **Indexing stays flat** (`b[k]`): no change to existing buffer semantics, zero
  break. Shape is advisory metadata read only by the tensor ops. (Tidepool's
  `policy_forward` never indexes tensors directly — only via matmul/add/relu — so
  this costs nothing.)
- `AotTensor` = `{ buffer.data, rows, cols }` — a view, no copy.

A new `VAL_TENSOR` type was rejected: it would touch every `switch(type)` in the
VM (print, `==`, refcount, indexing, observer, serialization) and need a fresh
AOT path — far larger, for general-ndim capability the current consumers (2-D MLP
/ attention) don't need.

## Decision 2 — v1 reach: Tidepool path first, then measure

Consumer op usage: **Tidepool** = matmul/add/relu; **iLambdaAi** = + transpose /
softmax / leaky_relu. v1 covers **2-D shape + matmul/add/relu**, migrates
Tidepool, and measures before committing to the rest. The full ~20-op tensor
surface is explicitly out of v1.

## Byte-exactness

The VM stays the oracle. Shaped-buffer `matmul` uses the existing `ne_matmul_buf`
(same i-k-j accumulation order as today), `add`/`relu` stay `num_guard`
elementwise — so the AOT's existing buffer-matmul tests (t20 etc.) already
validate the kernel. No accumulation-order change ⇒ byte-exactness preserved.

## Phases

1. **Upstream EigenScript** — `VAL_BUFFER` gains `rows/cols`; `zeros of [r,c]` and
   `reshape of [buf, r, c]` constructors; `matmul`/`add`/`relu` gain a
   shaped-buffer fast path that **skips the flatten** (dual-accept: still take
   nested-list too, transitionally). VM-only speedup is measurable here.
   Blast radius: the 7 files that touch `VAL_BUFFER`
   (builtins_tensor, eigenscript, ext_gfx, trace, builtins, jit, vm).
2. **AOT (ouroboros)** — `AotTensor` as a zero-copy view over a shaped buffer;
   kernels in place reusing output-axis SIMD + guard elision; byte-exact vs the
   Phase-1 VM.
3. **Tidepool migration** — `neural.eigs` weight storage → shaped buffers
   (`xavier_init`/`zeros_*` fill flat); `policy_forward` source unchanged. Measure
   VM (flatten gone) + AOT (~90× expected). This is the proof.
4. **Later (out of v1)** — transpose/softmax/leaky_relu flat kernels, migrate
   iLambdaAi, then **remove the nested-list tensor builtins**. The dual-accept of
   Phase 1 is scaffolding, not permanent backcompat — it is deleted here.

## Risks / verify

- Buffer **equality** and **serialization** with shape — define whether shape
  participates. The CBOR/tidelog path showed no direct buffer refs; verify buffers
  aren't serialized, else the format needs the shape fields.
- Confirm no Value-size growth in practice (union sizeof check).
- `transpose`/`softmax` flat kernels are Phase 4, not blocking Tidepool.
