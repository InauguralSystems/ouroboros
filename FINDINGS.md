# ouroboros — EigenScript findings

Language behaviors surfaced by writing a self-hosting compiler in EigenScript.
Classified: **BUG** (defect, fix upstream), **GAP** (missing primitive the
workload needs), **CONSTRAINT** (real limit with a clean in-language
workaround), **BY-DESIGN** (behaves as intended; recorded to prevent false
alarms).

---

## F-OURO-1 — no way to run an EigenScript-built chunk — GAP → FIXED upstream (PR #251)

`EigsChunk` is an in-memory C struct reachable only from C, with no
serialization and no execution entry point exposed to EigenScript. A compiler
written in EigenScript could produce bytecode but had no way to *run* it.

Added `vm_run_bytecode of [code, constants]` (PR #251): assembles a chunk from a
byte-int list + constant pool and runs it on the same `vm_execute` the C
compiler's output uses. This is the single primitive the whole bootstrap rests
on. The fifth primitive driven into the language by a consumer project (after
tidelog's #248/#249/#250).

Probe that proved feasibility before committing: hand-assembled `print of (2+3)`
ran; then `compile_ast`'s exact 44-byte `if/else` chunk was replayed and took
both jump directions (driven only by swapping the constant pool).

---

## F-OURO-2 — emitted chunks need not be byte-identical to compile_ast — BY-DESIGN (leverage)

Because `vm_run_bytecode` runs on the real VM, the back-end only has to emit
*semantically correct* bytecode, not a byte-for-byte copy of `compile_ast`'s
output. The codegen is free to choose its own constant ordering, slot
allocation, and instruction selection as long as the VM computes the right
result. This makes the behavioral oracle (stdout parity vs the C evaluator) the
right target and byte-identical chunks merely a stretch goal. `max_stack` is
likewise only a hint — the VM runs on a global value stack — so a hand-built
chunk can leave it unset.

---

## F-OURO-3 — observer assignment is separable from correctness (slice 1) — BY-DESIGN

`compile_ast` emits `OP_OBSERVE_ASSIGN` before `OP_SET_NAME` for every `is`
assignment (the entropy/dH observer update). Slice 1 omits it: a program that
doesn't query observer predicates computes identical results without it, so
stdout parity holds. This cleanly separates the conventional language (done) from
the signature observer/temporal subsystem (the deep slice still to come). When
ouroboros emits `OBSERVE_ASSIGN` / `INTERROGATE` / `PREDICATE` and matches the C
runtime's entropy semantics, parity will extend to observer programs — and any
divergence there is a sharp finding (meta gap, or a C bug).

---

## F-OURO-4 — reserved keywords can't be codegen identifiers — CONSTRAINT

Writing the compiler in EigenScript means its own variable names collide with the
language's reserved words. A jump-patch helper using `at` as a local
(`local at is len of c.code`) failed to parse — `at` is a temporal interrogative
keyword (`what is x at <line>`). Renamed to `hole`. A self-referential gotcha
unique to self-hosting: the metalanguage and object language are the same, so the
reserved set (temporal `at`/`prev`, predicates, interrogatives, `unobserved`,
etc.) is off-limits for compiler internals. No defect — recorded so later slices
avoid it. (Caught instantly because load_file now raises parse errors, PR #245.)

## F-OURO-5 — break/continue are compile-time jumps, not opcodes — BY-DESIGN

`OP_BREAK`/`OP_CONTINUE` exist in the enum, but `compile_ast` does *not* emit them
for ordinary loops: `continue` lowers to a `JUMP_BACK` to the loop header and
`break` to a forward `JUMP` to the loop exit, resolved at compile time. ouroboros
matches this with a loop-context stack (header offset + a list of break holes
back-patched at the exit). Emitting the opcodes instead left the loop running
(they rely on a loop mechanism the simple-loop path doesn't establish) — the
jump lowering is the correct model. A `for`-loop `continue` jumps straight to the
header, skipping `LOOP_ENV_END`; the VM tolerates this (the C compiler does the
same), so per-iteration env balancing is not required on the continue path.

---

## Slice 2 (control flow) — DONE

if/elif/else, `loop while`, `for`-in, `break`/`continue`, and short-circuit
`and`/`or`, via forward jumps (back-patched) and backward jumps, plus the #247
`LOOP_CAP_CHECK` safety cap. 12/12 programs at byte-identical stdout parity,
including nested loops. No new upstream primitive needed.

## F-OURO-6 — the bridge needs nested chunks + slot/name metadata — GAP → FIXED upstream (PR #251 follow-up)

Functions compile to *nested* chunks: `OP_CLOSURE [fn_idx]` references the parent
chunk's `functions[]` array, params/locals live in numbered slots
(`GET_LOCAL`/`SET_LOCAL`), the call frame is sized to `local_count`, and
`OP_CLOSURE` reads param names from the chunk's `local_names`. The flat
`[code, constants]` bridge couldn't express any of that. Extended
`vm_run_bytecode` to a recursive chunk descriptor:

    [ code, constants, functions?, param_count?, name?, local_names? ]

`functions` is a list of descriptors (recursive), `local_names` (slot order)
sizes the frame and supplies `OP_CLOSURE`'s param names. The 2-element form still
works. Same suite (2072/2072), ASan-clean.

## F-OURO-7 — EigenScript's calling convention spreads list-literal args — BY-DESIGN

`f of [a, b]` is a *multi-argument* call: compile_ast pushes `a`, `b` and emits
`CALL 2`, binding them to the callee's two param slots — `f of x` is `CALL 1`.
ouroboros matches: a call whose argument is a list literal spreads its elements
into positional args; any other argument is a single `CALL 1`.

## F-OURO-8 — function scope: `is` is local-or-outward by name resolution — BY-DESIGN

Inside a function, `name is expr` resolves like compile_ast's
`emit_assign_for_tos`: an existing local slot → `SET_LOCAL`; a name bound at
module scope → `SET_NAME` (mutate the outer binding); otherwise → a fresh local
slot. Reads are `GET_LOCAL` for known locals, else `GET_NAME`. ouroboros
pre-scans the module for top-level assigns/defines to seed the module-name set,
which is what makes `counter is counter + 1` inside a function mutate the module
`counter` while a function's own temporaries stay local.

**Two silent miscompiles here, both FIXED (the upstream re-review caught them; the
32-program suite missed them because no sample hit either case):**
- **for-loop variable vs. a local slot.** The `for` codegen unconditionally
  emitted `SET_NAME_LOCAL` for the loop var, but if that name already owned a
  local slot (a parameter, or an earlier assignment) the body read the slot via
  `GET_LOCAL` and never saw the iteration value (`define f(i) as: for i in
  [10,20]: …` → C 30, ouroboros 0). Now: a loop var that already owns a slot is
  written with `SET_LOCAL` to that slot, matching the slot-reading body and the C
  runtime (incl. the value persisting after the loop).
- **module name first bound inside a block.** `cg_scan_module_names` only walked
  *direct* top-level assigns/defines, so a global first created inside a
  module-scope `if`/`loop`/`for`/`try` was absent from the module-name set — an
  inner function then shadowed it with a fresh local instead of mutating it
  (`if 1==1: counter is 0` then `define inc()…counter…` → C 2, ouroboros 0). The
  scan now descends into those block bodies (not into function bodies — those are
  function scope). Reduced reproducers: `test/programs/for_var_slot_collision.eigs`,
  `test/programs/module_name_in_block.eigs`. (The `local` keyword is now tokenized
  by the front-end.)

---

## Slice 3 (functions and locals) — DONE

define, parameters, `return`, slot-allocated local variables, the `f of [..]`
arg-spread calling convention, module-variable read/mutate from inside functions,
and recursion. 17/17 programs at byte-identical stdout parity (incl. fact, fib,
loops-in-functions). Extended the bridge to nested chunks (F-OURO-6).

## F-OURO-9 — ouroboros over-spread single-element list args — BUG (in ouroboros; the differential oracle caught it)

A parity divergence first looked like a C runtime bug — a program that errored
in the C evaluator (`cannot index dict for assignment`) but ran in ouroboros:

    define f(xs) as:
        d is {}
        for w in xs:
            d[w] is 1
        return d
    print of ((f of [["x", "y"]])["x"])

Root-causing it (not the VM — the **calling convention**) flipped the verdict:
the defect was in *ouroboros*, not C. `compile_ast`'s rule is that a list-literal
argument spreads into positional args only when it has **>1** elements
(`f of [a, b]` → `f(a, b)`); a **1-element** literal does *not* spread —
`f of [x]` passes the one-element list `[x]` itself (and `f of []` is a zero-arg
call). ouroboros was spreading *every* list literal, so `f of [["x","y"]]`
unwrapped the inner list instead of passing `[["x","y"]]`. The C "error" was the
correct, intended behavior; ouroboros was wrong.

Confirmed decisively: making the C compiler spread 1-element literals too broke
**81** of the 2072 suite tests — the non-spread of `count==1` is load-bearing
across the stdlib. So C was reverted untouched and ouroboros's call codegen fixed
to match (`count != 1` list literals spread; `count == 1` and non-list args pass
a single value). A `call_convention` parity test now locks it.

The lesson is the oracle working *as designed*: a behavioral divergence is a
neutral signal, not proof of which side is right. Here it caught a self-hosting
codegen bug — which is exactly as valuable as catching an upstream one, and a
reminder not to assume the reference is the buggy party. No upstream change.

---

## Slice 4 (dicts, indexing, comprehensions) — DONE

dict literals, index get/set (`d[k]`, `xs[i] is v`), dot get/set (`d.f`,
`d.f is v`), nested indexing, and list comprehensions with optional filters
(`LISTCOMP_BEGIN`/`LISTCOMP_APPEND` + iterator + filter `JUMP_IF_FALSE`). 22/22
programs at byte-identical stdout parity with the C evaluator. No new upstream
primitive needed. The differential oracle caught a calling-convention bug in
ouroboros's own codegen (F-OURO-9), now fixed and locked by a parity test.

## Slice 5 (observer opcodes) — DONE

The distinctive EigenScript surface — where the self-hosted compiler stops being
conventional:

- **OBSERVE_ASSIGN / OBSERVE_ASSIGN_LOCAL** now precede every `is` store (the
  matching observe op with the same slot/name arg), so the observer state
  (entropy + dH window) is tracked on each assigned value and the last-observed
  variable is set. Resolves F-OURO-3 (slice 1 had omitted it).
- **PREDICATE <kind>** for the bare predicates `converged`/`stable`/`improving`/
  `oscillating`/`diverging`/`equilibrium` (kinds 0–5; the vendored front-end's
  `_predicates` order matches the VM's exactly). Verified firing: a converging,
  a Fibonacci-growth, and a constant sequence produce real `1`s
  (`diverging`/`improving`/`equilibrium`) — byte-identical to the C evaluator.
- **Loop-stall classifier (#247, correctness-critical):** a `loop while`
  condition that references a predicate compiles to `OP_LOOP_STALL_CHECK`
  (opt-in convergence auto-halt); a plain condition to `OP_LOOP_CAP_CHECK`. The
  classifier mirrors `cond_is_observer_based` (recurse unary/binop, predicate ⇒
  observer-based, everything else opaque). Both opcodes confirmed emitted for the
  right loops; halting behavior matches C.

25/25 programs at byte-identical stdout parity, including six firing predicates
over full 10-sample observer windows. No new upstream primitive needed.

### Out of scope (front-end / C grammar mismatch)

The **interrogatives** (`what`/`who`/`when`/`where`/`why`/`how`) and **temporal**
forms (`prev of x`, `what is x at <line>`) can't be parity-tested: the vendored
`eigen.eigs` front-end accepts `what of x` / treats `prev`,`at` as identifiers,
but the C grammar rejects `what of x` as an expression (`undefined variable
'what'`) and has its own `prev of` / `... at <line>` syntax the front-end doesn't
tokenize. So these diverge at the *parser*, not codegen — a front-end limitation,
not a back-end gap. (cf. F-TEMPORAL-1 in tidelog: the temporal system is its own
axis.) A future slice could extend the vendored front-end to match.

## F-OURO-11 — node-type coverage is necessary but not sufficient — METHOD

The pre-bootstrap audit asked "can ouroboros compile its own source?" and got a
false-positive from a node-type scan: both `codegen.eigs` and (after adding
`try`) `frontend.eigs` "compiled OK". But *compiling* a chunk only proves every
AST node type is handled — not that the bytecode is correct. The bootstrap smoke
test (actually *running* the self-compiled compiler) immediately exposed what the
scan missed: a `local` keyword the vendored front-end didn't tokenize, which it
silently misparsed into a stray `local` identifier reference (`undefined variable
'local'` at runtime). Lesson: coverage audits for a compiler must *run* the
output, not just compile it.

## F-OURO-12 — bootstrap fixed point (codegen) achieved — MILESTONE

Two real gaps closed for self-hosting:
- **`try`/`catch`** added to the codegen (`TRY_BEGIN`/`TRY_END` + catch handler).
- **`local`** added to the vendored front-end (tokenize + parse `local NAME is
  expr`); ouroboros already allocates a function-local slot for non-module names,
  so a plain assign node suffices.

With those, **ouroboros compiles its own `codegen.eigs`, and the resulting
self-hosted compiler produces byte-identical bytecode to the C-hosted original**
for a broad test program (fib, comprehensions-with-filter, dict build in a loop,
try/catch, `local`). Locked by `test/bootstrap.eigs` as a fixed-point oracle
(`ouro_compile(P)` before vs after self-compilation must be `==`).

## F-OURO-13 — full self-host achieved; the "front-end self-host bug" was a test artifact — MILESTONE / METHOD

The first attempt at self-hosting the *front-end* too appeared to fail
(`undefined variable 'a'`). Chasing it the same way as F-OURO-9 — isolate, don't
assume — every reproduction passed: the self-hosted front-end tokenized,
*parsed* (identical AST), and *compiled* both source files byte-identically. The
"bug" was an **unescaped quote in the throwaway test harness**: the test program
string contained `["a", "b"]` instead of `[\"a\", \"b\"]`, so the EigenScript
string literal terminated early and `a` became a stray identifier at the test
file's module scope. The compiler was never wrong. (Second time this slice a
divergence pointed away from the real cause — F-OURO-9 blamed C, this blamed the
self-hosted parser; both were elsewhere. The discipline that pays off: reproduce
minimally before believing the diagnosis.)

With a correctly-escaped harness, **the full both-halves bootstrap is a byte-exact
fixed point**: ouroboros self-compiles its front-end *and* codegen, and the fully
self-hosted compiler reproduces the bytecode of its front-end, its codegen, and a
test program byte-for-byte — verified by `test/bootstrap.eigs`. The language
reproduces its entire toolchain.

## Slice 6 (interrogatives + temporal) — DONE

Closed the front-end grammar mismatch with C. The vendored front-end's old
`<kw> of x` interrogative syntax (which C rejects) is replaced with C's real
grammar, and codegen + a small upstream primitive complete the loop:

- **Front-end:** parse `<kw> is x [at <line>]` (what/who/when/where/why/how) and
  `prev of x`; reserve `prev` and `at`. Node `["interrogate", kind, expr,
  at_expr]`, kinds 0–6 (6 = prev).
- **Codegen:** three-way opcode selection mirroring `compile_ast` —
  `INTERROGATE_NAMED_AT` for the `at` form on an ident, `INTERROGATE_NAMED` for
  who/when/prev on an ident, `INTERROGATE` (value-based) otherwise.
- **OP_LINE:** ouroboros now emits `OP_LINE` per statement (from `_line`
  wrappers) — needed because `... at <line>` reads per-line history; also fixes
  error-message line numbers. Behavior-neutral for stdout, so the bootstrap fixed
  point and all parity tests still hold.

### F-OURO-14 — temporal queries need runtime history; the bytecode bridge didn't carry the signal — GAP → FIXED upstream

`prev of x` and the `at` forms read per-assignment history, which the C compiler
enables as a compile-time side effect (`g_trace_hist`, plus `g_trace_obs_hist`
for the observer-state forms `where/why/how is x at <line>`). The bytecode alone
doesn't carry that signal, so a self-hosted program's temporal queries returned
`null`. Added the upstream builtin **`record_history of flag`** (sets both
history flags); ouroboros's codegen tracks `USES_HISTORY` and `ouro_run` calls
`record_history of 1` before running such a program — mirroring how the C
compiler auto-enables it. All interrogative and temporal forms now match the C
evaluator byte-for-byte.

## AOT tensor-value layer + the flatten-bound finding

The AOT now compiles **verbatim** tensor-builtin neural code — Tidepool's exact
`policy_forward(policy, obs)` (a 3-layer MLP built from the `matmul`/`add`/`relu`
builtins over a dict-stored `policy`) compiles and is **byte-identical** to the
VM at full dims (433→64→32→6). This needed an `AotTensor` handle (flat
row-major `double*`) bridging the runtime's nested-list tensor Values, kernels
byte-exact vs `builtins_tensor.c` (raw i-k-j matmul, guarded elementwise add,
clamp relu), dict-field access, tensor params/returns, and list/dict literals.

### F-OURO-15 — the AOT's speedup lives where the VM INTERPRETS, not where it already calls native builtins

Measuring the verbatim policy forward: **VM 2.91s → AOT 2.65s over 4000 forwards
= ~1.1×**, NOT the ~90× a buffer-loop *rewrite* of the same math shows. Two
reasons, both load-bearing for "where is the AOT useful":

1. **The VM's `matmul`/`add`/`relu` are already native C** (`ne_matmul_buf` &c).
   The 90× only appears when the same math runs as *interpreted element loops*.
   Where the VM dispatches to a native builtin, it is already near-native and the
   AOT has almost nothing to take.
2. **The workload is flatten-bound.** Each call flattens the 27,712-element
   nested-list `w1` (≈110M boxed reads / 4000 calls) — a cost **both** the VM and
   the AOT pay. The matmul flops are cheap by comparison. Isolated single-matmul:
   VM 2.69s vs AOT 2.46s = 1.09×. The AOT's only edge is skipping intermediate
   *rebuilds* (small here).

The weights are loop-invariant, so caching the flatten would win — but that's
unsound in general (training mutates weights in place; a pointer-cache goes
stale). **So the dramatic AOT speedups belong to FLAT-BUFFER storage + explicit
loops, not to nested-list tensors + builtins.** The tensor layer is a real
capability gain (consumer code compiles byte-exact; mixed code still gets the big
multiple on its interpreted-scalar parts), but the honest rule is: the AOT
accelerates code the VM *interprets*. The verbatim bench corrected the proxy
bench — the measurement-is-the-moat trap, caught in the act.

### F-OURO-16 — the flat-buffer matvec is bandwidth-bound; output-axis SIMD regresses it

Phase 2b set out to add output-axis SIMD + guard elision to `aot_tensor_matmul`.
Neither applied:

- **No guard to elide.** `ne_matmul_buf` (the VM kernel the AOT matches) is
  *already raw* — the matmul accumulation never calls `num_guard` (the downstream
  `add` guards). So the AOT matmul is unguarded by construction; there is no
  `num_guard` to remove (unlike the user-written buffer-loop matmul of #31).

- **SIMD made it slower.** A hand-rolled output-axis SIMD (vectorize the output
  column `j`, accumulate across `k` in registers) measured **0.144s vs 0.100s**
  for the 433→64→32→6 forward (4000×, SSE2). Reason: it puts `k` innermost, which
  **strides `b` by `cols`**. The plain i-k-j form keeps `j` (the output column)
  innermost — a *contiguous* axpy `o[i,:] += a[i,k]*b[k,:]` that the compiler
  auto-vectorizes and that sweeps `b` linearly, cache-perfectly. For a batch-1
  matvec the kernel is bound by streaming the weight matrix, not by SIMD compute,
  so the contiguous form already wins.

The matmul was therefore left as the contiguous i-k-j (with a comment recording
this so it isn't "optimized" back into a strided SIMD regression). **The Phase-2
speedup was fully captured by the zero-copy view (2a): ~2.8× over the VM, ~28×
over the original nested-list VM** — removing the per-call flatten and the
builtin-dispatch/refcount overhead, not the matmul inner loop.

### F-OURO-17 — the AOT now compiles a full real observer program (dynamics/life.eigs), byte-exact

The earlier dynamics assessment concluded the AOT was a numeric/buffer **subset
compiler**: the observer *primitives* were byte-exact (the t27–t36 harness), but
*real* observer code — built from those primitives via strings, lists, functions,
for-loops, and observed function-locals — didn't compile. That verdict no longer
holds. `dynamics/life.eigs` (Conway's Life: scalar `report` vs the temporal
signature that actually distinguishes a blinker from a block) now AOT-compiles
and runs **byte-identical to the VM, end to end.**

Seven gaps were closed, one byte-exact PR each, with `life.eigs` as the
forcing-function oracle (each fix advanced it exactly one gap):

| gap | what it took |
|---|---|
| value-context `not`/`and`/`or` | `and`/`or` short-circuit returning the operand, not a bool |
| list/string locals, returns, `append` | non-numeric local/return typing + the `append` direct-borrow ref |
| value-context indexing `x[i]` | `aot_index_get` mirroring `vm_index_get` (negative/bounds) |
| `unobserved:` block | bracket the body with the runtime depth counter |
| `for var in iter` | materialize + walk; `collect_assigns` descends for-bodies |
| strings (f-strings, `==`, concat) | already worked; 3 surrounding typing fixes |
| observed functions | per-function observation + env-param seeding |

The observed-function gap was the architectural one and surfaced a debugging
cascade — segfault → infinite loop → `e-310` garbage → byte-exact — each step
localized from the generated C plus a minimal repro:

- A user variable named `g` (life's loop counter) shadowed the emitted global
  `Env* g`, passing a `long` where the Env was expected. The emitted Env was
  renamed to a reserved `__eigs_g`.
- An observed loop var was int-typed, so the *read* used the bare C name (a stuck
  spurious local) while the *write* went to the env — a non-incrementing counter.
  Observed functions no longer int-type their locals.
- A **list**-returning function was treated as buffer-producing, so its result was
  indexed as a `double[]` → garbage. Only buffer-*returning* functions are now
  buffer-producing (`retbuf`); a boxed-list index in numeric context reads via
  `aot_index_get`.

The AOT is still a deliberate subset (one boundary remains guarded, not built:
*nested* observed functions need a per-call env), but "real observer code doesn't
compile" is no longer the boundary.

### F-OURO-18 — AOT soft-keyword frontend support is ready but PIN-GATED — TRACKING

`fuzzdiff.py` found real second-parser drift: the canonical EigenScript parser
(`parser.c`, on EigenScript **main** / `[Unreleased]`) now binds the soft
keywords `prev`/`at` and the six question words as ordinary identifiers in
binding positions, but the AOT frontend (`src/frontend.eigs`) still rejected
them (`unexpected token kw 'at'`). Commit **2adcb31** fixes this — mirrors the
canonical rule in `frontend.eigs`, adds `aot/test/t49_soft_keyword_idents.eigs`,
and isolates the fuzzer's soft-keyword generator from the separate compound-assign
gap (`at += N` → `at is at + N`). It passed locally (full AOT parity + self-host
bootstrap; fuzzdiff clean).

It was **reverted** (d359dec) because the feature is NOT in the pinned VM:
`.devcontainer/Dockerfile` pins `EIGS_REF=v0.19.0`, whose VM rejects soft-keyword
identifiers, so the patched AOT *over-accepts* relative to its oracle and t49
diverged in CI. Per policy, the AOT must byte-match the **pinned** VM, not main.

ACTION (do this with the next `EIGS_REF` bump): when `EIGS_REF` moves to a release
that includes the soft-keyword feature, `git cherry-pick 2adcb31` to re-land the
frontend fix + t49. The cherry-pick also restores the `at += N` → `at is at + N`
generator isolation in `fuzzdiff.py` — **keep it**, or the fuzzer will conflate
"soft keywords accepted" with "compound assignment unsupported" (a separate,
still-open AOT emitter gap). Run `fuzzdiff.py` with `EIGS` pointed at the pinned
VM, never local main.

---

## F-OURO-19 — front-end silently SKIPPED unknown characters → silent-wrong; now RAISES — FIXED

The lexer's catch-all `else: # Unknown character — skip` (frontend.eigs) dropped
any byte it didn't tokenize and advanced — so a construct built from an
unsupported character lexed to a *different, valid-looking* token stream and
compiled to a silently-WRONG program instead of erroring:
- `x += 3` lexed as `x`, `+`, `3` (the `=` skipped) → the expression `x + 3` was
  computed and discarded; `x` never changed.
- `~5` lexed as `5` (the `~` skipped) → printed `5`, not `-6`.
- bitwise `& | ^` were skipped likewise.

The C lexer *rejects* these characters (`unexpected character`); the AOT emitter
genuinely does not yet support compound-assignment / bitwise operators, but the
front-end must FAIL LOUD rather than miscompile. The catch-all now
`throw`s `ouroboros: unexpected character '<c>' at line N`, matching the C
lexer's reject behavior. These can't be **parity** cases (the C VM *compiles*
`+=`/bitwise), so they live as **reject** cases in `test/run.sh` (ouroboros must
exit non-zero). Implementing the operators is the remaining follow-up; this
closes the silent-wrong half. Validated against the pinned v0.19.0 VM: 34
parity + 3 reject + bootstrap fixed point all green.

## F-OURO-20 — front-end OVER-ACCEPTED `true`/`false` as boolean literals → silent-wrong; now plain identifiers — FIXED

The front-end lexed `true`→literal `1` and `false`→literal `0` (a keyword-token
pair, plus parser primaries returning `["num", 1]`/`["num", 0]`). **EigenScript
has no boolean keywords** — the C lexer has no `true`/`false` token; they are
ordinary identifiers and the language uses `1`/`0`. So ouroboros diverged from
the C oracle three ways:
- `print of true` → C: `Error: undefined variable 'true'`; ouroboros: `1`.
- `x is true` → C: error; ouroboros: `x = 1` (silent miscompile).
- `true is 5` (a valid assignment to the name `true` in C) → C: `5`; ouroboros:
  `parse error: unexpected token kw 'is'` (a keyword can't be an lvalue).

Fix: drop `true`/`false` entirely (no back-compat) — removed from `_keywords`,
the lexer's literal-emission elifs, and the parser's primary handling. They now
fall through to the `ident` path and behave as plain names, byte-for-byte with
the C evaluator (undefined unless bound; assignable like any identifier). The
front-end never used them itself, so the bootstrap fixed point is unaffected.
Permanent positive parity case: `test/programs/true_false_are_identifiers.eigs`
(uses them as bound names). Validated against the pinned v0.19.0 VM: 35 parity +
3 reject + bootstrap fixed point all green. (Same silent-wrong class as
F-OURO-19; F-OURO-13: the premise was reproduced minimally before fixing.)

## F-OURO-21 — `+=`/bitwise operators IMPLEMENTED (were reject-only) — DONE

The C VM compiles compound assignment (`+= -= *= /= &= |= ^= <<= >>=`) and the
bitwise operators (`& | ^ ~ << >>`), but ouroboros only *rejected* them
(F-OURO-19 made the front-end fail loud on the unknown characters rather than
silently miscompile). That left a real coverage gap: ouroboros did not cover the
full language the C VM accepts. Now implemented end to end:

- **Lexer** (frontend.eigs): tokenizes `& | ^ ~ << >>`, the two-char compound
  ops `+= -= *= /= &= |= ^=`, and the three-char `<<= >>=` (a new longest-match
  three-char pass precedes the two-char checks).
- **Parser** (frontend.eigs): four new left-associative precedence levels
  inserted between `comparison` and `add` — `bitor | -> bitxor ^ -> bitand & ->
  shift << >>` — mirroring the C chain (src/parser.c). EigenScript's precedence
  is NOT C's: bitwise binds *tighter* than comparison (`4 | 1 == 5` is
  `(4|1)==5` = 1), and shift is looser than `+` (`1 << 2 + 1` = `1 << 3` = 8).
  Unary `~` added alongside `-`. Compound assignment desugars `x += e` ->
  `x is x + e` (AST `["assign", name, ["binop", base, ["ident", name], e]]`),
  matching C's compound_to_op — so it needs NO new codegen.
- **Codegen** (codegen.eigs): `OP_BAND/BOR/BXOR/SHL/SHR` (9-13) in
  cg_binop_code, `OP_BNOT` (16) for unary `~`.

The three former reject cases (`~5`, `6 & 3`, `x += 3`) became the parity
program test/programs/bitwise_ops.eigs (full op set + precedence + bitwise
compound assign), byte-exact vs the C VM. The reject section now uses
genuinely-unknown characters (`@`, backtick, `$`) that both the C lexer and
ouroboros still error on — preserving the F-OURO-19 fail-loud guarantee.
Validated: 36 parity + 3 reject + bootstrap fixed point all green. The bootstrap
fixed point holding is the key check — the front-end+codegen, extended with the
new operators, still reproduce their own bytecode byte-for-byte (self-host
preserved). ouroboros now covers the operator surface of the C VM.

## F-OURO-22 — frontend drift vs the canonical parser closed (pin-safe subset of #57) — FIXED

A differential pass (issue #57) found two silent-class diverges and four
over-rejections in src/frontend.eigs vs the canonical C parser at the
EIGS_REF pin v0.21.2. All six pin-safe items are now implemented:

- **Hex literals** (silent-wrong): the number lexer now mirrors strtod's hex
  acceptance — `0x/0X` + hex digits, hex fractions (`0x10.f` = 16.9375, digits
  a-f count), lone trailing dot (`0x2.` -> 2), binary exponent `p/P` with the
  same lookahead guard as `e` (`0x1p` -> 1 + ident `p`), and `0x` with no hex
  digit lexing as `0` + ident, exactly like strtod.
- **Dot-postfix on literals** (silent parse-acceptance divergence): postfix
  now lives per-primary inside _p_parse_primary, mirroring parse_primary's
  per-kind loops — idents/parens (incl. desugared f-strings)/dicts take
  `.field`+`[idx]`; num/str/list literals and the question-word fallback take
  `[idx]` only; listcomps/null/predicates and the prev/at fallback take NONE;
  a call result (`f of x`) takes no postfix (C's parse_relation returns it
  directly). `[10,20].x` / `"ab".foo` are now parse-rejected like C, and
  `[x for x in l][0]` splits like C (the `[0]` is a discarded statement).
- **Compound assignment on dot/index targets**: `d.m *= 4` desugars re-reading
  the target (C clones the subtree — evaluated twice); `l[i] += e` carries the
  base op as a 5th index_assign element and codegen lowers it via OP_DUP2 →
  INDEX_GET → rhs → binop → INDEX_SET, so target/index evaluate ONCE (proved
  by a side-effecting-index parity case). The AOT emitter throws LOUDLY on the
  compound form (not yet lowered there).
- **F-OURO-18 re-land** (2adcb31, prev/at + question words in binding
  position): cherry-picked now that the pin (v0.21.2) contains the v0.20.0
  soft-keyword change. One deliberate correction to the original: the
  dot/index-assignment lookahead stays gated on plain idents (C gates it on
  TOK_IDENT), and the prev/at identifier fallback takes NO postfix — at the
  pin `prev[0]` splits into `prev` + a discarded `[0]` (upstream #328 changes
  this on main; that alignment waits for the next EIGS_REF bump).
- **Destructuring** `[a, b] is rhs`: statement-level bracket-count scan
  committed on `] is`, identifiers-only pattern (C rejects soft keywords,
  index/field targets, trailing commas), exact-length runtime check via
  OP_DESTRUCTURE_UNPACK + per-name stores. Over-long rhs errors like C
  (a naive desugar to indexed reads would have silently accepted it).
- **Parameter defaults** `define f(x, k is 2)`: parsed like C (trailing-only,
  required-after-default is a parse error), fires only for MISSING args
  (explicit null stays null), default expr evaluated at call time in the
  callee env. Codegen emits the same OP_DEFAULT_PARAM prologue as the C
  compiler. **Runtime gotcha discovered:** the pinned VM pre-allocates env
  slots only when local_count > param_count, and OP_SET_LOCAL *silently
  drops* writes to slots >= env->count — so a defaults prologue in a function
  with no body locals wrote into a nonexistent slot on an underfed call.
  codegen pads one never-read local slot ("__defaults_pad") to force the
  reserve. The AOT emitter throws loudly on defaults (its calling convention
  has no argc).

NOT implemented (deliberately): statement-terminator enforcement (upstream
#326) — unreleased at the pin; enforcing it now would over-reject vs the
pinned oracle.

New parity programs: hex_literals, compound_index_dot, destructuring,
param_defaults, soft_keyword_binding (41 programs + bootstrap green); three
new reject cases (`[10,20].x`, `"ab".foo`, `5 .foo`); AOT harness 49/49 green
against the pinned runtime. The bootstrap fixed point holding again proves the
extended front-end+codegen still reproduce their own bytecode byte-for-byte.
