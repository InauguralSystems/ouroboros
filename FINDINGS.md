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

### F-OURO-18 — AOT soft-keyword frontend support is ready but PIN-GATED — DONE (re-landed at the v0.21.2 bump; #328 postfix alignment landed with v0.23.0 — t49/t50 pin both)

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

NOT implemented at the v0.21.2 pin (deliberately): statement-terminator
enforcement (upstream #326) — unreleased at that pin; enforcing it then would
have over-rejected vs the pinned oracle. Landed with the v0.23.0 bump below.

New parity programs: hex_literals, compound_index_dot, destructuring,
param_defaults, soft_keyword_binding (41 programs + bootstrap green); three
new reject cases (`[10,20].x`, `"ab".foo`, `5 .foo`); AOT harness 49/49 green
against the pinned runtime. The bootstrap fixed point holding again proves the
extended front-end+codegen still reproduce their own bytecode byte-for-byte.

**Pin bump to v0.23.0 (follow-up):** EIGS_REF moved v0.21.2 → v0.23.0 and the
two mirrors deferred above landed:

- **Statement terminator (upstream #326)**: `_p_end_statement` now runs at
  the simple-statement return points (assign, compound assign, destructure,
  local, return, break, continue, import, expression statement) — leftover
  tokens are a parse error, "one statement per line". Block statements
  (if/for/loop/define/try/match/unobserved) consume their own DEDENT and
  deliberately do NOT get the check. New reject case `x is 2 x is 3`;
  reject_one now also asserts the C oracle rejects, so a reject case can't
  rot into a valid program the front-end wrongly refuses.
  **Upstream #326 gap found (surface upstream):** the C parser's DOT-/INDEX-
  assign paths return WITHOUT the terminator check — `d.k is 2 3`,
  `d.k += 5 6`, `l[0] is 8 9`, `l[0] += 1 4` all silently DISCARD the
  trailing token at v0.23.0 (verified vs the oracle; parser.c's member-
  assignment lookahead has no p_end_statement). The frontend mirrors the gap
  (oracle wins — enforcing there would over-reject) and locks it in with
  parity program stmt_terminator_gap.eigs, which doubles as a canary: it
  starts failing the moment a future pin closes the gap.
  **Closure:** the canary fired at the v0.24.0 pin bump — upstream #351 closed
  the dot-/index-assign gap, so stmt_terminator_gap.eigs was retired and the
  cases now live as reject_one entries in test/run.sh (which assert the C
  oracle also rejects).
- **Soft-keyword postfix (upstream #328)**: the prev/at identifier fallbacks
  take the FULL dot+bracket postfix chain, and the question-word fallback is
  full postfix too (dot AND bracket — verified vs the oracle: `how.k` works
  in a `for how in [{"k":1}]:` loop). Consequence, verified vs the oracle:
  `prev[0] is 9` at statement level is a PARSE error at v0.23.0 (the fallback
  takes the postfix as an expression; the leftover `is` hits #326) — the
  dot/index-assign lookahead stays gated on plain idents. New parity programs
  soft_keyword_postfix.eigs + aot/test/t50_soft_postfix.eigs;
  soft_keyword_binding.eigs's old "fallback takes no postfix" tail updated.
- **`__defaults_pad` STAYS** (upstream #348 chose runtime-error over
  auto-reserve): out-of-range OP_SET_LOCAL now raises "SET_LOCAL slot N out
  of range" instead of silently dropping, so the pad is the legitimate slot
  reservation — without it the defaults prologue would raise on every
  underfed call. Comment updated; defaults verified against the new oracle.

## F-OURO-23 — AOT lacks a whole-list user-fn param class; #355 paren args throw LOUDLY — FIXED (#64 inc. 2)

The v0.24.0 bump mirrors upstream #355 (parens always mean one argument) in
`frontend.eigs` (a parenthesized literal list carries a 3rd marker slot) and
`codegen.eigs` (marked lists never spread) — the self-host tier proves full
parity (`test/programs/paren_no_spread.eigs`). The AOT emitter honors the
marker at both user-fn call sites, but its param specialization has no
generic "whole list" class (`func_ptypes`: num default / dict / tensor /
buf from body usage), so `f of ([a, b])` to a user function emits
`emit_num(list)` → **loud build-time throw** ("emit_num on non-numeric node
list"). That failure mode is correct per the contract (loud beats silent
spread, which is what pre-mirror emission would have produced — a silent
semantics divergence from the v0.24.0 oracle). Lifting the limit means a
generic `Value*` param class inferred when call sites pass non-numeric
wholes; do it when a real AOT consumer needs it. No AOT-tier test can pin
the new semantics in-envelope (whole-list args, defaulted params, and
under-arity calls are all unsupported there — each throws loudly), so the
self-host program IS the parity proof for this bump; the existing tN suite
pins that bare spread is unchanged.

**v0.27.0 bump update (upstream #405, one call rule):** a bare literal list
after `of` is now ALWAYS an argument list at every count, so `f of [x]`
passes ONE arg (the element) and is **in-envelope** — the AOT's dedicated
1-element throw ("call f of (x), not [x], for a single arg") was removed
from `emit_args`, and `codegen.eigs` dropped its count!=1 no-spread guard
(mirroring the dropped count>1 guard in the canonical `compiler.c`).
Whole-list args to user fns now arise only via the #355 paren form
`f of ([x])`, which still hits the `emit_num(list)` loud build-time throw
described above — the TRACKING status (no generic `Value*` param class) is
unchanged. `test/programs/call_convention.eigs` pins the new rule at the
self-host tier.

**#64 inc. 2 update — the generic `Value*` param class lands (FIXED):**
`func_ptypes` gains class `"gen"`: a param whose body dispatches on
`type of x` (checked before the buffer rules — `len of x` beside a type
dispatch used to classify buf and die on string input at aot_buf_len's
runtime guard), or that is passed onward at an already-analyzed function's
gen position (`find_gen_param_use`, one-pass in definition order like the
buf propagation; a miss stays num and fails loudly at the call site). A
gen param is a C `Value*` kept BOXED: reads route through the existing
value machinery (emit_val hands out an incref'd name; builtin calls go
through `aot_call_name`, indexing through `aot_index_get`, arithmetic
through the binfn value ops), so any runtime type the VM accepts flows
byte-exact — string-or-buffer polymorphism costs nothing new. A gen name
in a PURE-numeric C context joins the buf/dict/tensor loud build throw
(the VM raises there for non-numbers; a number reaching it is
conservatively rejected too — loud beats aot_num's silent 0.0), and
REBINDING a gen param throws (the env-set fallback would leave the C name
stale — a silent-divergence class). `return`ing a gen param whole makes
the function `Value*`-returning (`return_node_is_boxed`). With this, the
whole checksum surface — `_blen`/`_byte` type dispatch, str AND buffer
args to user fns, `ord`/`char_at`/`buf_get`/`buf_len` on generic
operands, the #355 paren whole-list form — compiles and matches the VM
byte-for-byte (CRC-32/Adler-32/sum8 on the pinned vectors), plus 150
fuzzdiff programs with 0 divergences / 0 gaps. `aot/test/
t54_generic_params.eigs` pins the class, the propagation, and the paren
whole-list arg. The README bench (n=5 medians, same PR) closes #64's
acceptance: verbatim checksum ~2.0× (the boxed generic path keeps the
VM's dispatch overhead), buffer-monomorphic variant ~17× — the generic
class buys COVERAGE (real polymorphic stdlib code compiles at all), the
specialization ladder stays the multiplier.

## F-OURO-24 — hex literals became a LEXED form upstream (#378); frontend follows at the next pin bump — FIXED (v0.25.0 bump)

EigenScript #378 (merged to main 2026-07-03, UNRELEASED — not in the
v0.24.0 pin) ends the strtod delegation for hex: the canonical lexer now
consumes `0x`/`0X` + hex digits itself, on every profile, and the
accidentally-accepted hex-FLOAT forms (`0x10.f`, `0x1p4`) are loud parse
errors instead of numbers. (Found by EigenOS M12: the freestanding
mini_strtod has no hex path, so `0xFF` parsed hosted and lexed as `0` +
ident `xFF` on bare metal.)

`frontend.eigs` today mirrors the v0.24.0 oracle exactly — hex ints AND
hex fractions AND p/P binary exponents (the lookahead-guarded block near
line 312) — so per the pin rule there is NOTHING to change yet: switching
early would flag false drift against the pinned VM. When `EIGS_REF` moves
past #378, in the SAME bump:
- drop the hex-fraction and p-exponent paths (hex digits only; `0x` alone
  still lexes as `0` + ident `x`, which #378 keeps);
- add parity programs: hex-int forms (case, adjacency, `0xFF+1`) plus
  `reject_one` cases for `0x1p4` / `0xA.8` (the C oracle now rejects them
  too, so reject parity is assertable);
- re-run both harnesses against the new pinned oracle.

EXECUTED with the v0.25.0 bump (2026-07-04): frontend.eigs lexes hex
integers itself (digit accumulate via _hex_val — `num of "0x…"` no
longer involved, so the value path is profile-independent too); the
fraction/p-exponent paths are deleted; the decisive-prefix behavior
(`0x`/`0x.8` → `0` + stray ident, loud) falls out of the plain-number
fallthrough with no special case. Frontend review during the flip also
CAUGHT AN UPSTREAM RESIDUE: #378's first cut still let glibc strtod see
`0x.8` (hosted 0.5, freestanding parse error) — fixed upstream in the
same release (decisive-prefix in lexer.c; suite [50b] gained rejects
for 0x1p4/0x.8/0x), so the reject_one cases here assert against a
genuinely closed oracle. hex_literals.eigs re-cut to the integer-only
contract; reject_one gains the three forms.

## F-OURO-25 — for-in loop vars were unreadable in numeric contexts; `f of null` emitted zero-arg calls — FIXED (#69, #70)

Both found while building the #67 keyword test; both were LOUD build
breaks, not silent divergence.

**#70 (loop var):** the `for` emitter binds the loop var in the env
(`aot_set` each step), but `emit_num`'s ident case emitted a bare C name
for every unobserved ident — valid only for names declared as C doubles
(module globals in `nm`, numeric params/locals in `fnm`). A numeric-context
read of a loop var (buffer index, `index_assign` RHS) emitted an
undeclared identifier. Fix: `emit_num` idents not in `nm` read boxed via
`aot_num(aot_get(...))`, mirroring `emit_val`'s ident path. Fixing that
unmasked the value-context half: `emit_val`'s index case routed EVERY
target through the env, so a C-local buffer indexed by a boxed numeric
(`t + u[j]`) fetched null — now a `bt` ident target reads elementwise via
`make_num(aot_buf_get(...))` (same VM index semantics, loud on error).

**#69 (null call):** every user fn has >= 1 param — a zero-param
`define f()` gets an implicit unused `n` from BOTH parsers (parser.c and
frontend.eigs agree) — yet `emit_args` lowered a `null` argument to zero
C args, so `f of null` (the only way to call the zero-param idiom) was
always invalid C ("too few arguments" vs the `(double n)` signature). Fix:
when the single param is never read in the body (`nullok`, computed at
registration), the call site passes a dummy 0; a callee that READS its
param rejects `of null` with a loud build-time throw (the VM binds null,
which has no C numeric equivalent — guessing 0 would be the silent-wrong
outcome this repo forbids).

`aot/test/t51_nullcall.eigs` + `t52_forvar_numctx.eigs` pin both. Bench
note while validating: a stale `aot/build/libeigsrt.a` compiled from local
main (7 commits past the pin, incl. upstream #465 observer changes) made
t32_report diverge — false drift, gone once the lib was rebuilt from the
pinned worktree. The lib cache is mtime-keyed, and a freshly-added
worktree has OLDER mtimes than a lib built minutes before, so switching
`EIGS_DIR` does NOT auto-rebuild: `rm aot/build/libeigsrt.a` when moving
between runtimes.

## F-OURO-26 — bitwise operators land in the AOT (#64 increment 1); two silent-wrong classes made loud/correct on the way — FIXED (#73, #74)

The checksum forcing function (#64) drove four connected changes:

**Bitwise infix + unary (~).** `& | ^ << >>` emit the VM's INT_BINOP
exactly: int64 two's-complement over the numeric value, shift counts
masked to 0..63, and the final `(double)` cast is the VM's own last step
(always finite → no num_guard, byte-exact by construction). Unary `~` is
OP_BNOT. #73 was the pre-existing silent-wrong here: emit_num's unary
case treated every non-`-` op as logical not, so `print of (~5)` gave VM
-6 / AOT 0 — emit_num and emit_val now dispatch unary ops explicitly and
throw on anything unknown. `t53_bitops.eigs` pins the semantics
(masking, negative operands, precedence, boxed loop-var operands, the
CRC-32 inner loop).

**#74 — one signature authority.** The forward proto typed params from
registration-time `iparams` (index-forced only) while the definition
used `infer_int` (also int-by-assignment, e.g. the shadowing `local n is
0`) — gcc "conflicting types" on `_crc_init`. emit_function now records
its computed sig in `g_fsigs`; definitions are emitted first (into a
buffer) and protos are DERIVED from the recorded sigs, so the two can
never disagree. `iparams` is gone.

**Value-typed C names vs the env.** A buffer/dict/tensor param or local
is a C variable, NOT an env binding — but emit_val's ident case read the
env for every name (silent null: `type of x` on a buf param always took
the string branch), and after the #70 boxed fallback emit_num would have
done the same (aot_num(null) = 0.0, a wrong number). Now: emit_val hands
out the C name (increfed; tensors serialize via aot_tensor_to_value);
emit_num THROWS for Value-typed names in a numeric context (the VM
raises there). This is what turned the checksum probe from
compiles-and-prints-garbage into a loud build error at `_byte`.

**Buffer class guards.** The buf param class is inferred from usage, so
an out-of-envelope input (string reaching a buf param — checksum's
polymorphic `_blen`) misread the value union silently. A str/num literal
to a buf param now throws at BUILD time; the aot_buf_* helpers type-check
at runtime (cold, predictable branch — in-envelope programs never take
it): `h of s` with s a string global now dies "buffer op on a non-buffer
value" instead of printing union garbage.

Zero-arg calls: `f of []` (the #405 one-call rule, checksum's
`_crc_init of []`) joins `f of null` in the #69 lowering, and nullok is
now `param_unread` — the incoming value is dead if the body rebinds the
param (straight-line prefix scan) before any read, which is exactly the
`_crc_init` idiom. Branch-local first assignments deliberately don't
count (conservative → loud).

Still open for #64: the generic Value* param class (string-or-buffer
polymorphic params — `_blen`/`_byte` dispatch on `type of x`), `ord`/
`char_at`/`buf_get`/`buf_len` builtins on generic operands, and str args
to user fns. The probe now fails loudly at the first of these.

Negative cases (zero-arg-to-reading-callee, str-literal-to-buf-param,
runtime non-buffer guard) are verified manually — the AOT harness has no
reject tier yet; worth adding one when the envelope work continues.

## F-OURO-27 — builtin call sites didn't mirror the one call rule: a 1-element bare list passed a 1-wrapper, a SILENT wrong value — FIXED (#64 inc. 2)

The v0.27.0 bump (#68) mirrored upstream #405 at USER-fn call sites
(`emit_args`) but not at builtin call sites: `emit_val`'s fallback emitted
`aot_call_name(name, <literal list>)` for any bare literal list. The VM
packs builtin args as: 1 arg → the raw value, 0 or >1 → a list — so for 0
and >1 elements the literal-list emit is coincidentally equivalent, but a
1-element bare list diverged: `buf_from_list of [[49, …]]` handed the
builtin `[[49…]]` (a 1-wrapper) where the VM hands it the inner list. The
checksum probe caught it as compiles-and-prints-wrong-numbers — the exact
worst-outcome class this repo forbids — CRC/Adler/sum8 of a buffer built
that way were all wrong while the string paths were byte-exact. The fix
lowers a 1-element bare literal list to its ELEMENT at the
`aot_call_name` emission site (paren-marked #355 lists still pass whole,
matching the VM). Pinned by t54's `buf_from_list` construction; the
zero-arg builtin case (`f of []` → empty list on both sides) was already
equivalent and unchanged.

## F-OURO-28 — spec_audit lands: the tape names the AOT's missed specializations, with evidence instead of guesses (#65 inc. 1)

PGO against ephemeral samples is the incumbent move; ours is auditable —
an EIGS_TRACE tape is byte-exact, replayable, committable profile
evidence. Increment 1 is the instrument: `compile.eigs --dump-inference`
prints one deterministic record per name (scope, role, inferred storage
kind mirroring the ASSIGN path's actual dispatch, first-assignment line),
and `aot/tools/spec_audit.eigs` joins that dump against a tape's
`A name=value` records: a name inference boxed whose every traced value
was numeric is a MISSED site, ranked by assign count (the profile
weight). Both stages are byte-for-byte deterministic on the same inputs
(verified by double-run diff).

**Tape discovery en route:** docs/TRACE.md says `A` records track
top-level bindings, but function locals ALSO land on the tape — by name
only, no scope qualifier (checksum probe: `c` ×2324 aggregating
`_crc_init.c` + `crc32.c`). Counts therefore aggregate across same-named
scopes, but numeric-STABILITY survives aggregation (all values numeric ⇒
each scope's values numeric), so function-scope verdicts are sound and
reported as MISSEDLOCAL with the aggregation caveat; mixed-type
aggregates are AMBIG (no attribution), never guessed. Upstream doc drift
to surface.

**First evidence set (checksum probe + the full tN corpus):** the
dominant missed class is the for-in boxing cascade — the loop var is
env-boxed by design and every accumulator whose RHS reads it (or an
env-boxed list index, or a value-semantics and/or) is demoted too, while
the tape proves them stably numeric (t42: 10 sites; t47/t52: 4 each;
t49/t53: 2 each; t39: 3; everything else fully specialized — also useful:
the audit CONFIRMS full specialization on 40+ programs). Top-ranked site:
**crc32's `c` (line 53, 2324 assigns)** — precisely the variable behind
the checksum bench's 2.0× generic vs 17× monomorphic gap, now named by
runtime evidence. That demotion chain (`is_num_expr` rejects
boxed-target index reads → the accumulator goes env-boxed) is #65
increment 2: fix it, prove n=5, close the acceptance loop.

## F-OURO-29 — the numeric-or-raise rule: spec_audit's top finding fixed, measured, and the loop closed (#65 inc. 2, DONE)

The fix for F-OURO-28's top-ranked site is a CLASSIFIER upgrade, not a
special case. `is_num_expr` now claims a binop under any op the VM defines
ONLY for numbers (`- * / % & | ^ << >>` — oracle-verified: no string/list
repeat, no coercion, every non-numeric operand raises "cannot apply")
REGARDLESS of operand classification; `+` (polymorphic: str+str concat)
is claimed when EITHER side is provably numeric; `and`/`or` with both
sides numeric return the operand via a short-circuit C ternary. The
greatest-fixpoint pass therefore stops demoting accumulators whose RHS
reads a boxed name — the for-in cascade and crc32's env-list table read.

Soundness hinges on LOUD boxed reads: a boxed operand now emits through
`aot_num_ck` (error+exit where the VM raises) instead of `aot_num`'s
silent 0.0 — the boxed-ident (#70) and boxed-index paths were upgraded
too, converting a pre-existing silent-tolerance into a faithful loud
failure (planted fault: `[1,"a"]` element through `*` dies at the same
point on both tiers, exit 1, after identical prior output). The AOT has
no try/catch, so a VM-catchable raise can't diverge. `emit_num` gained
the generic boxed-call fallback (`aot_num_ck(emit_val(call))`, reusing
the one-call-rule arg packing) for builtins in numeric contexts.

Two regressions caught in-increment by the harness, one latent: the rule
pulled `len of xs - 1` (xs a boxed LIST) into `is_int_expr`'s
unconditional len-of claim → `aot_buf_len` on an undeclared C name
(t45 BUILD FAIL). is_int_expr/emit_int now gate len-of on a new g_btmap
scope mirror (they read globals, unlike the bt-threaded emitters); the
dot/sum/norm/len emit_num fast paths got the same bt gates, with
is_num_expr's dot claim tightened to match exactly (a wider claim than
the emitter intercepts = infinite recursion through the fallback).

Results: probe re-audit **1 → 0 missed** (crc32's c is a C double);
corpus re-audit rescues every demoted accumulator (t39 3→0, t42 10→5,
t47 4→2, t52 4→2, t49 2→1, t53 2→1) — every survivor is the for-in
loop var itself (boxed by design; the next lever, now cleanly isolated).
Measured n=5 medians: verbatim checksum 2.70s → 2.14s (−21%,
byte-identical), ~2.5× over the VM. Both harnesses + 150 fuzzdiff
programs green against the pin. t55 pins the rule (cascade accumulator,
env-list-element arithmetic, boxed len shapes, numeric and/or operand
semantics, str+str unaffected). #65's acceptance is complete: tool on
2+ real programs, top site fixed with an n=5 win, byte-deterministic
audit.

## F-OURO-30 — for-in loop vars unbox to C doubles; two pre-existing post-loop divergences surfaced by the oracle probes — FIXED (spec_audit follow-on)

The last MISSED class the audit isolated (boxed-env-for) is gone for
unobserved/untraced programs: when the iterable provably yields numbers
(the range builtin un-shadowed, a C buffer, or an all-numeric list
literal), the loop var binds a C double per iteration — the env
write/read round-trip disappears from the loop. The materialization is
KEPT (aot_iter_len/aot_iter_get over the built Value), so range's exact
semantics (fractional/negative args) ride the runtime's own builtin; a
buffer iterable reads elementwise via aot_buf_get_i; list elements take
aot_num_ck (never fires in-envelope — the recognizer proved them
numeric). A name bound by both an eligible and an ineligible loop is
POISONED and stays boxed everywhere (two loops sharing a name must agree
on storage); a for-var naming a BOXED module global stays boxed (the VM
mutates it via SET_NAME — a local C double would silently shadow); and
observed/traced programs keep the boxed path (slots and history must see
the bindings — t49's `prev of x` makes its numeric `what` loop var
correctly stay boxed, and the audit now reports that honestly rather
than as a missed win).

Scope semantics are ASYMMETRIC in the VM, which the oracle probes pinned
down (and shipped main got wrong in one case):
- FUNCTION scope: the var owns a slot; post-loop reads see the last
  iterate. Unboxed vars get a function-top C decl — byte-exact (t56's
  last_of/reuse), including a param reused as the loop var.
- MODULE scope: the VM LOOP-SCOPES the binding — a post-loop read raises
  'undefined variable' even after a non-empty loop. The boxed emission
  silently served the stale last value (a pre-existing silent divergence
  on main, found by probing during this work). Unboxed module vars are
  declared INSIDE the loop's C block, so a post-loop read is an
  out-of-scope name — a loud BUILD error where the VM raises at runtime.
  Unrecognized (still-boxed) module loops keep the old silent behavior —
  narrower now, noted here rather than silently tolerated.
- Pre-existing and UNCHANGED: reading a function for-var after a
  ZERO-iteration loop gives VM null vs AOT 0 (the boxed path already
  diverged the same way via aot_num(env-miss)). Pathological shape; on
  the ledger, not worth a Value-typed loop var.

Audit re-run: t42/t47/t52/t53 all report zero missed; the only corpus
survivor is t49 (traced — correctly boxed). Measured n=5 medians on a
2M-iteration module for-in accumulator: AOT 0.75s → 0.55s (−27% vs the
boxed emission, byte-exact both ways), VM 1.09s → the AOT is now ~2.0×
there. Residual cost is the materialized range list + per-element
incref/decref — open-coding range iteration is the next lever if a
consumer needs it. t56 pins the shapes (range/list/buffer iterables,
the poisoned mixed-name case, post-loop function reads, param reuse,
nested loops); the module post-loop build-refusal is verified manually
(no reject tier yet, same note as F-OURO-26).

## F-OURO-31 — first full adversarial review of the self-host tier: 8 silent-wrong/silent-accept miscompiles vs the v0.39.0 oracle — FIXED (#99); the still-unsupported envelope is now named

External review + differential probes (#99) found eight live divergences,
all in the repo's named worst-outcome class (silent wrong values or silent
acceptance, none previously on this ledger). All eight are fixed by
mirroring the pinned parser.c/lexer.c, each with a case that fails on
pre-fix main:

1. `not` sat between `and` and comparison; parse_unary_body puts it at
   UNARY tightness. `not x + 1` was `not (x+1)` — silent wrong values in
   BOTH compilers (shared frontend). test/programs/not_unary.eigs +
   aot/test/t63_not_unary.eigs.
2. The `local` qualifier was dropped to a plain assign, so a `local`
   shadowing a module name MUTATED the module binding. The assign node now
   carries a 4th local_only element; cg_store_name mirrors
   emit_assign_for_tos (existing slot wins; a no-slot `local` on a module
   name binds by NAME in the frame env via OP_SET_FN_NAME_LOCAL — exactly
   C's route, so nested closures and `prev of` see the shadow; module
   scope emits SET_NAME_LOCAL). The AOT refuses a module-shadowing
   `local` loudly (its plain-assign path writes the file-scope C global);
   non-shadowing `local` is unchanged-correct.
   test/programs/local_shadow.eigs.
3. The catch variable ignored an existing local slot (F-OURO-8's class):
   catch bound by name while reads went via GET_LOCAL — stale value.
   Fixed twice over: the slot-check from the `for` emitter for param
   names, plus the env-bound pre-scan (below) which keeps non-param catch
   names off the slot path entirely, so `prev of e` reads real history.
   test/programs/catch_slot.eigs.
4. Unterminated string/f-string/brace-expression at EOF lexed silently,
   swallowing every following line at rc=0; now loud like lexer.c
   ("unterminated string"). must_reject cases.
5. A dedent matching no outer indent level was accepted (block structure
   silently changed); now loud like lexer.c. must_reject case.
6. Interrogative/temporal drift: operands and at/when qualifiers are full
   parse_expression (was primary — `who is y + 1` over-rejected); the
   #868 `when <N>` qualifier is mirrored (OP_INTERROGATE_NAMED_WHEN 93;
   vm_run_bytecode's chunk_arm_temporal arms the occurrence ring itself,
   #831); `prev of <literal>` raises "'prev of' requires a variable name"
   (#634) instead of silent null. test/programs/interrogative_expr_when.eigs
   + must_reject.
7. F-string brace bodies with leading whitespace spliced a stray indent
   token; the sub-lex now drops indent/dedent like the C lexer (#334).
   test/programs/fstring_brace_ws.eigs.
8. Token-class drift: `for`/listcomp expected ANY keyword where C expects
   TOK_IN exactly (`for x of` was accepted — must_reject now), catch
   expects the `catch` keyword; lambda params take the full
   tok_is_ident_like set (soft keywords — `(prev) => prev + 1` was
   over-rejected); `%=` lexes and desugars end to end.
   test/programs/lambda_soft_params.eigs + compound_mod_assign.eigs.

ROUND 2 (blind-critic review of PR #105): the first cut mirrored only the
SLOT arm of emit_assign_for_tos and missed its escape pre-scan — the C
compiler keeps three name classes OFF the slot path and binds them via
OP_SET_FN_NAME_LOCAL (frame env, skipping loop envs), because slot-locals
are anonymous at runtime: invisible to the name-keyed history/observer
opcodes (INTERROGATE_NAMED/_AT/_WHEN) and to nested closures' GET_NAME.
Four oracle-backed divergences fell out (`when` on a fn local → null; a
nested define reading a `local` shadow → module value; `prev of` a local
shadow → null; `prev of` a catch name → null). cg_func now runs
cg_scan_name_bound (the mirror of scan_for_captures +
scan_for_interrogated + scan_for_env_bound): names referenced by nested
define/lambda bodies (minus their own params), ident operands of any
interrogate form (every kind incl. prev), and catch/listcomp names bind
by name; params keep slots (they ARE name-visible in the VM call env —
resolve_local wins first in C too); everything else stays slot-fast.
This also fixed `prev of` on ANY plain function local (round 1 had it
down as a pre-existing tail). Pinned by
test/programs/interrogate_fn_scope.eigs, lambda_capture.eigs and the
round-2 sections of local_shadow.eigs / catch_slot.eigs — each fails on
the pre-scan-less first cut. The lambda arm (OP_CLOSURE descriptor,
expression body) now supports captures through the pre-scan; its loud
guard remains only for the theoretically-unreachable non-param-slot
escape.

ROUND 3 (blind-critic review of the round-2 cut) finished the mirror —
two more misses, both critic-confirmed against the pin:
(a) the in_outer arm was absent: a nested define writing an ENCLOSING
function's name allocated its own fresh local (silent wrong; on pre-#99
main the read side was a loud undefined-variable — round 2's pre-scan
alone had converted loud to silent). cg_new now carries the lexical
`enclosing` chain and cg_name_in_enclosing mirrors name_in_enclosing
(each enclosing FUNCTION's slot locals + name-bound set; the walk stops
at module scope). cg_store_name's function-scope arbitration is now
emit_assign_for_tos's, in order: (1) existing SLOT wins (C resolves
locals before every escape check); (2) pre-scanned NAME-BOUND →
OP_SET_FN_NAME_LOCAL; (3) not in_outer, not in_module AND not in_globals
→ local-eligible, fresh anonymous slot (`local` included); (4)
ineligible + `local` → OP_SET_FN_NAME_LOCAL (the explicit shadow); (5)
ineligible plain assign → OP_SET_NAME (outward mutation of the
enclosing, module or GLOBAL binding). The in_globals term landed in
round 5 (critic round 4): C consults the compile-time global env —
which for a main-program compile is the BUILTIN registry — so
`define f as: len is 5` really clobbers the global `len` (VM-verified;
the mirror had fresh-slotted it, a pre-existing silent-wrong on main).
cg_is_builtin probes the runtime registry via a cached
type-of-eval check; ouro_compile is never behind a load_file/import
module boundary, so C's g_compile_module_boundary disable never applies.
test/programs/builtin_name_write.eigs pins the plain-write, nested-write
and local-shadow shapes; builtin_name_clobber_err.eigs pins the
acceptance shape (both sides die "cannot call num" after the clobber) —
both failing at the round-3 cut.
test/programs/enclosing_scope.eigs pins read-modify-write,
write-only, two-level nesting, sibling isolation, param shadow, and
local-shadow + nested write — all byte-exact, failing at the round-2
cut.
(b) the unified pre-scan walker descended into listcomps for the
interrogated class, but C's scan_for_interrogated has an explicit
AST_LISTCOMP no-op — an interrogate operand appearing ONLY inside a
listcomp body must NOT name-bind the outer variable, and the VM really
answers null there (`[prev of y for v in xs]` on a slot-local y). The
walker now carries the boundary (captures and env-bound still descend,
matching scan_for_captures / scan_for_env_bound).
test/programs/listcomp_interr_boundary.eigs pins the null AND the
arming-interplay contrast (an outer interrogate name-binds y, after
which the inside query answers) — oracle-verified, failing at the
round-2 cut.

STILL-UNSUPPORTED envelope at the self-host tier, previously untracked:
`match` (OP_MATCH), `import` (OP_IMPORT) and `unobserved:` (the signature
perf lever, EigenScript#915) throw LOUDLY at compile; predicate-of value
forms (`converged of x`) die LOUDLY at runtime ("cannot call num" — the
VM's OP_PREDICATE_NAME path is not emitted); of the appended observer
opcode family (PREDICATE_SLOT/NAME 87/88, OBSERVE_VALUE_SLOT/NAME 84/85,
REPORT_*, TRAJECTORY_*) only INTERROGATE_NAMED_WHEN 93 is emitted.
CAUTION — one form is NOT loud: `report of x` compiles as a plain builtin
call and SILENTLY answers "equilibrium" where the VM classifies for real
(pre-existing, reproduces on pre-#99 main) — that, plus bare
interrogative statements being accepted where C compile-errors, is
tracked in #106. The AOT additionally refuses, loudly at build time:
`when` (no occurrence-ring seam), module-shadowing `local`, and — round
6 — a plain function-body write to a BUILTIN name (the in_globals class:
VM semantics clobber the global binding, the AOT's plain-assign path
would make a C local — a silent wrong value on pre-#99 main; `local
<builtin>` shadows, params named after builtins, and builtin reads keep
working). aot/test/run.sh still has no reject tier (F-OURO-26's note),
so the three refusals are verified manually per change; the exact
refusal lines are recorded in PR #105. Growing any of these is new
work, not a bug; this entry is the ledger naming them.

## F-OURO-32 — the AOT silent-wrong family falls: full VM comparison semantics, per-call envs for boxed locals, builtin-return typing, loud negation/observed reads, clean uncaught-error death, tensor shape guards — FIXED (#100, #103)

The 2026-08-16 adversarial review (#100) named a connected family of
missed branches in the loud-throw discipline; all landed in one pass,
byte-exact-beats-loud-beats-silent:

1. **AOT_CMP is the VM's NUM_CMP now.** num/num compare, str/str
   byte-wise strcmp with the operator applied to the strcmp result, and
   the VM's exact `cannot compare X and Y with 'op'` (EK_TYPE) on any
   other pair — the old macro answered 0 for EVERY non-num/num pair
   (`"a" < "b"` → 0 silently; `1 < "a"` ran past a VM stop). Type names
   mirror slot_type_name: null reports "none" on every oracle-probed
   path (literal, dict miss, list element). t64 (parity) + t65 (_err).

2. **Boxed function locals get a per-call env.** String/list/dict/null
   locals and still-boxed for-in vars lived in the ONE global env:
   recursion clobbered outer frames (`f(2)` printed inner0/inner0/inner0
   for inner0/1/2) and locals leaked into module scope (`print of msg`
   after the call printed the value where the VM raises). Each function
   with boxed locals now allocates `Env* __eigs_l = env_new(NULL)` per
   invocation, freed on every return path (emit_return wraps returns;
   the value is computed into a temp BEFORE the env dies). Read tiers
   mirror the VM's opcodes: __eigs_l misses are GET_LOCAL null (unset
   slot — branch-conditional first assignment); __eigs_g reads go
   through aot_get_named, which raises the VM's `undefined variable`
   (GET_NAME) instead of the old silent make_null. Module-bound names
   (any gnm/gbt key) deliberately stay global — a plain fn assign to one
   is the VM's outward mutation. Observed/temporal functions keep Part
   2a global routing (their existing guard rejects the nesting/recursion
   that clobbers). `local` shadowing ANY module binding (boxed globals
   included now, not just numeric/buffer) refuses loudly at build. This
   unblocks #86's dpll_rec class. t67 (parity) + t68 (_err).

3. **func_ret_type sees builtin returns and string concatenation.**
   is_boxed_node now consults BOXED_RET_BUILTINS (`return trim of v`
   declared double, aot_num returned 0 where the VM returns "hi") and
   claims `+` chains carrying a string literal (the fuzz seed-6 shape:
   `tag is "a" + (str of n); return tag` — same silent 0 through the
   local's rhss). What still reaches the double-return coercion dies
   loudly via aot_num_ck_at, never aot_num's silent 0. t69.

4. **Observed-mode ident reads are checked** (the one branch F-OURO-29
   missed): `aot_num(aot_get(...))` → `aot_num_ck_at(env_read(...))`.
   A string reaching arithmetic in an observed program dies where the VM
   raises `cannot apply` (AOT message text differs — verified manually,
   same point, same rc, like F-OURO-29's planted faults).

5. **emit_val unary `-` is OP_NEG**: aot_neg negates a number or raises
   the VM's exact `cannot negate non-numeric`; the old aot_num coercion
   printed 0 for `-s` on a string. t66 (_err).

6. **Uncaught-error death is a clean exit(1) (#103).** Root cause
   verified by inspection: rt_error's uncaught print path calls
   vm_print_stack_trace whose first read is `(*eigs_current->vm)
   .frame_count` — a native binary never attaches a VM, ->vm is NULL,
   so every error-class death printed the message then SIGSEGV'd
   (rc 139, core dumps). aot_boot now elevates g_try_depth to 1 for the
   whole process (rt_error records, never prints, never touches the
   NULL VM); the AOT prints g_error_msg itself and exits 1 — the VM's
   uncaught-error code — at every error seam: the rt_error macro wrap
   in aot_rt.h, and a g_has_error check after builtin/fn dispatch in
   aot_call_name (a raising BUILTIN used to hand back null and the
   program ran on — the run-past sibling). Two builtin flag protocols
   surfaced by the check: `exit of N` unwinds via g_has_error TOO — the
   AOT used to run straight past it (printed the post-exit line, exited
   0); it now honors g_exit_requested/g_exit_code (t73). An uncaught
   `throw of` (builtin_throw — same NULL-VM print path) now dies
   cleanly at rc 1 with the VM's message (t74). Both AOT-tier gates now
   compare _err death codes for EQUALITY (aot/test/run.sh + fuzzdiff;
   test/run.sh's must_reject deliberately checks nonzero-both, not
   rc equality).

7. **Seven dead #86-era inference walkers deleted** (find_str_builtin_use,
   find_numeric_use, find_for_iter_use, find_value_pos_use,
   find_str_concat_use, find_append_use, find_dict_param_use — each
   referenced only by its own recursion, superseded by widen_ptypes;
   ~190 lines). chain_has_str and find_numeric_use_direct stay (live).

8. **Tensor runtime-dim guards** (the dict-field dims path, t18/t37
   shape): matmul mismatch raises the VM's #512 error verbatim
   (`matmul: incompatible shapes (1x2 · 3x2)`, EK_VALUE) instead of a
   silent null tensor serializing to `[]`; add mirrors the VM where the
   flat kernel can (equal shape; equal COUNT for buffer/buffer — the
   VM's fast path keys on count; min-count for 1-D/1-D lists — the VM
   truncates to the shorter) and REFUSES loudly at runtime on the
   remaining broadcast/mixed-kind shapes, where the old kernel read
   b.data past its end (OOB heap read, printed garbage). t70 (_err) +
   t71 (parity).

Follow-on found by the fuzz seeds: the boxed-index NUMERIC path forced
the index through emit_num, so `d["a"] + 5` (string key in a numeric
context) refused to build; the index is emitted as a VALUE now and
aot_index_get dispatches like the VM. t72.

STILL LOUD-REFUSED after this PR (the envelope): everything in
F-OURO-31's list (match/import/unobserved/when, module-shadowing
`local` — now including boxed module globals — builtin-name clobber,
defaults, under/over-arity, whole-list args), tensor `add` broadcast
and mixed buffer/list shapes (VM broadcasts or collapses to scalar 0.0
— the flat kernel refuses), and observed/temporal functions calling
observed/temporal functions (per-call env Part 2b). Known-remaining
silent divergence, pre-existing and narrower than before: a still-boxed
MODULE-scope for-var read after its loop serves the stale last value
where the VM loop-scopes (F-OURO-30's residual); `report of x` answering
"equilibrium" (#106).

## F-OURO-33 — numeric `local` shadows of module globals compile to C block-scope locals; the bench corpus is gated — FIXED (#109)

The #105/#107 module-shadowing-`local` refusal (correct: the plain-assign
path wrote the file-scope C global the VM shadows) had swallowed both #64
checksum benches, and nothing covered `bench/` so the loss was invisible.

**What changed.** A `local NAME` shadowing a NUMERIC module global now
declares a fresh C local (`double`/`long`) AT THE `local` STATEMENT'S
SITE — C block scoping from that point on IS the VM's chain-walk shadow,
oracle-probed at v0.39.0: reads/writes before the `local` line hit the
module binding (probe: read-before → module value; plain-write-before →
module mutated); everything after hits the shadow; a self-referencing
decl RHS reads the module value (staged through a C temp — in C a
declared name is in scope in its own initializer, in the VM the RHS
evaluates before binding); recursion gets a fresh shadow per call.
A `local` of a PARAM name (previously refused when the name was also
module-bound) is the VM's existing-slot-wins rebind — the C param
already shadows the file-scope var, so plain emission is exact.
aot/test/t75_module_local_shadow.eigs pins all of it (refused-to-build
on pre-#109 main). Both checksum benches build and run byte-exact again.

**What C block scope canNOT reproduce — validated and refused loudly**
(sh_validate_block/sh_container_ok in compile.eigs): the VM shadow
OUTLIVES its block (probe: nested-in-loop `local k`, read after the
loop — VM answers the shadow's last value, a C local would answer the
global), and iteration carry (an occurrence before the decl inside the
decl's own loop reads the previous iteration's shadow). So a nested decl
must DOMINATE every occurrence of the name; a depth-0 (function-body
top-level) decl additionally admits occurrences before it (they run
exactly once, pre-binding — and the name's int typing is dropped so the
pre-decl global reads go through double). Also refused: a nested
define/lambda referencing the name (probe: the VM's nested fn sees the
encloser's LIVE shadow; a lifted C fn reads the file-scope global and a
capbind capture is a stale snapshot), more than one `local NAME` per
function (C would collide or layer where the VM rebinds one binding),
observed/temporal functions (history/env state is keyed by NAME), and
any non-numeric assignment to the shadow (the seeded fnm would keep it
on the numeric path — the silent-wrong class).

ROUND 2 (blind-critic review of PR #110) — a BINDER occurrence of the
shadow name is not a chain-walk write, and the first cut treated it as a
benign post-decl touch: `local n is 55` then `for n in range of 3` —
VM prints 0/1/2 then 55 (the for-var is a fresh LOOP-SCOPED binding,
the shadow keeps 55), the round-1 emitter wrote the loop var through
the shadow's C name and printed 2 (silent wrong; pre-train this shape
was loud-refused). sh_binds now refuses for-var, catch-var and
comprehension-var re-bindings of the shadow name anywhere in the body —
role-based, so the guard holds even where today's envelope refuses
catch/listcomp for other reasons (probe: the comprehension var LEAKS in
the VM — `[n * 2 for n in [1, 2]]` after `local n is 5` leaves n == 2 —
a third distinct role semantics, confirming refusal over per-role
emission). The critic also found the PRE-EXISTING module-scope twin —
module-level `for n` over an EXISTING module global serves the global
all four prints where the VM loop-scopes (0/1/2 then 100) — filed as
#111 (reproduces at cef9522; not part of this train).

**STILL LOUD-REFUSED (unchanged from #107):** `local` shadows of BOXED
(string/list/dict) and BUFFER module globals, and everything else in
F-OURO-31/32's envelope list.

**Bench gate (part 2).** aot/test/run.sh grew a transpile-check tier:
every `bench/*.eigs` must build rc=0 (FAIL names the program); zero glob
matches is itself a FAIL (no vacuous pass). Planted-fault proven both
ways: a bench program using `match` → suite rc=1 naming it; `bench/`
moved aside → "examined ZERO programs" FAIL. Running the benches stays
out of the gate (minutes of wall time).

**Re-measured #64 numbers** (v0.39.0 pin, n=5 medians): generic
6.01s VM / 2.07s AOT = ~2.9× (was 2.0–2.5×; the VM side slowed ~13%
across pins), mono 1.36s / 0.092s = ~14.9× (was 17×). The round-1 guess
that the #107 seams cost the mono ~15% was REFUTED by measurement
(round 2): a mono binary built at the pre-train commit 3c4dde8 medians
0.0923s vs 0.0916s at HEAD — statistically identical — so the delta vs
the old 0.08s row is the original row's pin/measurement conditions, not
the correctness train. aot/README.md's table updated accordingly.

## F-OURO-34 — AOT binaries never set the resolution anchors: load_file/import's non-cwd chain was dead — FIXED (#86 endgame)

**BUG (fixed).** `resolve_eigenscript_file_from_ex`'s candidate chain is
anchored on two EigsState dirs that only the CLI's `main.c` sets:
`g_script_dir` (dirname of argv[1]) and `g_exe_dir` (dirname of
/proc/self/exe, whose `../lib` is the stdlib in a source checkout).
`eigs_open` leaves both at the `"."` default and `aot_boot` never touched
them, so in a native binary every step of the chain except bare
cwd-relative was dead: `load_file of "lib/int_vector.eigs"` failed
anywhere the cwd didn't happen to contain the file. This was the #86
frontier after #107 unblocked the EigenMiniSat transpile — the built
minisat died at `load_file: cannot read 'lib/int_vector.eigs'`.

**Measured VM order (v0.39.0 probe, 2026-08-16, conflicting candidates
planted at each anchor):** (a) cwd-relative wins first, then (b) the
script's own dir, script parent, and (c) the exe-dir stdlib roots
(`g_exe_dir/../<path>`, `g_exe_dir/../lib/eigenscript/<path>`, lib/-
stripped variant, `~/.local/lib/eigenscript`). Matches the source chain
in `builtins_host.c:resolve_eigenscript_file_from_ex`.

**THE RULE:** the AOT binary resolves exactly the files the VM would
resolve for `eigenscript <original .eigs>` run from the same cwd. Both
anchors are baked at BUILD time (`build.sh` → `PDEFS` →
`AOT_SCRIPT_DIR`/`AOT_EXE_DIR` → `aot_boot` snprintf): the absolutized
dirname of the compiled program, and the absolutized `$EIGS_DIR/src` of
the runtime it linked — NOT the native binary's own runtime location,
because the binary is a stand-in for the original program and its
imports live next to the SOURCE and that runtime's stdlib. The
cwd-relative first step stays runtime-dependent, identically on both
sides. Documented consequence: moving/deleting the source tree breaks
the binary's load_file exactly the way it breaks the VM invocation it
mirrors. The defines ride PDEFS on the gen.c compile only — DEFS is
hashed into the cached-lib stamp, and a per-program define there would
rebuild the runtime lib on every target change.

**Test:** `aot/test/t76_load_file_paths.eigs` loads a sibling-dir lib
(`test/data/`, invisible to the flat harness glob — script-dir step) and
stdlib `lib/int_vector.eigs` (exe-dir step), with the harness running
both sides from `aot/` — a different cwd than the program's dir. Proven
failing at origin/main cd86040: AOT died `load_file: cannot read
'data/t76_lib.eigs'` rc=1 while the VM printed all four lines rc=0.

**Runtime-tier note for consumers — MEASURED:** load_file'd modules
execute on the embedded bytecode VM inside the native binary
(builtin_load_file compiles and `vm_execute`s at runtime) — the AOT
compiles only the entry program's own statements. A load_file-structured
consumer like EigenMiniSat therefore runs its core at VM speed under
AOT. Measured (v0.39.0 pin, --cdcl, n=5 medians, taskset -c 0, SSE2
devbox): AOT-EMS is **0.93–0.96x of VM-EMS** across Tseitin 3x3–3x7
(e.g. 3x7: VM 49.94s vs AOT 52.52s), counters byte-exact on all five
plus a 36-run fixtures/corpus sweep (18 files x default+--cdcl, zero
divergences). Whole-program native compilation of multi-file consumers
is the next frontier; probing it with a concatenated single-file EMS
hits a LOUD envelope refusal — `AOT: \`local cdcl_opts\` shadowing a
module binding is not supported` (lib/solver.eigs's dict-valued opts
shadow, the boxed-module-global `local` class F-OURO-33 left refused).

## F-OURO-35 — boxed (string/list/dict) `local` shadows of module globals: per-call-env bindings with RUNTIME chain dispatch — FIXED (#86 increment)

The `AOT: \`local cdcl_opts\` shadowing a module binding is not supported`
refusal (F-OURO-32/33's boxed leftover) was the concrete blocker for
compiling the concatenated single-file EigenMiniSat.

**The design.** Where #109's numeric class maps the VM shadow onto C
block scope (decl-at-site, with the sh_validate_block occurrence
discipline that refuses whatever block scope cannot reproduce), a BOXED
shadow reuses #107's per-call env `__eigs_l` and moves the chain walk
to RUNTIME: `aot_get_sh`/`aot_getb_sh`/`aot_set_sh` (aot_rt.h) dispatch
on `__eigs_l` binding presence — bound → the shadow; unbound → the
module binding in `__eigs_g` (loud GET_NAME reads, SET_NAME-style
writes). The `local` decl itself is a plain `aot_set(__eigs_l, ...)`,
and env_get returns C NULL only for "unbound" (a bound null is a
VAL_NULL Value), so binding presence IS the VM's frame-vs-module
resolution. Consequence: NO occurrence discipline is needed — every
dynamic shape the numeric class had to refuse is exact by construction,
each oracle-probed at v0.39.0 and pinned in t77–t79 (all three proven
refused at origin/main aaaa916):

- pre-decl reads AND field/plain writes hit the module binding
  (`print of d["k"]` then `d.k is 2` before the decl);
- iteration carry: an occurrence before the decl inside the decl's own
  loop reads the previous iteration's shadow (VM probe: 0/10/11 then 12);
- a nested decl OUTLIVES its block (decl in an `if`, read+dot-write
  after it);
- two `local NAME` decls rebind the ONE frame binding;
- recursion gets a fresh shadow per call (freed on every return path by
  the existing #107 machinery; a returned shadow is computed into a temp
  before `env_decref(__eigs_l)`);
- a numeric re-assign of a boxed shadow rebinds it (`opts is 5` then
  `opts + 1` — the RHS boxes through the aot_set_sh fallback; the
  shadow name is force-stripped from fnm/g_int so an all-numeric-assign
  shadow can't drift onto the C-double path);
- `local x is <call returning boxed>`, two boxed shadows per fn, and a
  boxed + numeric (#109) shadow in one fn (t78).

**STILL LOUD-REFUSED** (verified manually per F-OURO-26's no-reject-tier
note): a nested define/lambda reading the shadow name (a lifted C fn
sees only `__eigs_g`; a capbind capture is a stale snapshot — same as
the numeric class), #110's binder rule (for/catch/comprehension-var
re-binding of the shadow name — the VM loop-scopes a binder OVER the
shadow), observed/temporal functions (Part 2a keys env/history state by
NAME), shadows the inference classifies buffer/tensor (their storage is
a C variable the env cannot see, e.g. `local d is buffer of 4`), and
`local` of a module BUFFER global. A boxed-RHS `local` over a NUMERIC
module global stays refused too, on the #109 path — loudly but with the
generic `emit_num on non-numeric node` message (pre-existing; the
seeded lnm keeps a numeric-global name numeric, so the "non-numeric
assignment" branch never fires for it).

**Concat-EMS outcome (the acceptance probe).** Single-file EMS =
stdlib int_vector + dimacs + solver + text_builder + bench + minisat,
load_file lines stripped, in execution order. (1) The shadow refusal is
GONE. (2) Next build-time stop: `AOT: \`negative is ...\` inside a
function writes the GLOBAL builtin binding` — EigenMiniSat
lib/bench.eigs:655 plain-assigns the builtin name `negative`
(F-OURO-31 round 6 refusal, correct; consumer-fixable with one
`local`). (3) With that one line patched, the whole 4,459-line program
**transpiles, compiles and links, rc=0**. (4) At runtime it dies LOUDLY
(rc=1, the F-OURO-32 discipline) at the known element-class frontier:
default mode `non-numeric value in a numeric context at
propagate_units: name clause (type list)`; `--cdcl`
`non-numeric element in \`watches\`[0] (list)` — the indexable-boxed
vs indexable-numeric split already named in #86's history. Two
trivially-UNSAT --cdcl fixtures (unit_unsat, satlib_trailer_unsat)
already run byte-exact end-to-end. No full solve → no timing number
yet; the next #86 increment is the element-class split, not shadows.

Gates: aot/test/run.sh (t77–t79 added) + test/run.sh green at the
v0.39.0 pin; fuzzdiff 40 programs seed 42 → 0 divergences / 0
run-pasts / 0 gaps.

## F-OURO-36 — the element-class frontier falls: concat-EMS runs byte-exact native, both modes — FIXED (#86 increment)

The concatenated single-file EigenMiniSat (F-OURO-35's acceptance probe:
stdlib int_vector + EMS dimacs + solver + stdlib text_builder + bench +
minisat, load_file lines stripped, one `local negative` consumer patch)
built rc=0 but died runtime-loud in both modes. Three distinct inference
gaps, each minimized to a ~10-line repro proven failing at origin/main
fbfd517 and fixed at the root (the F-OURO-17 one-gap-per-fix pattern
held exactly — each fix advanced the consumer to the next stop):

1. **For-in loop vars over a boxed-class PARAM are invisible to
   call-site widening** (t80, `non-numeric value in a numeric context at
   propagate_units: name clause (type list)`, default DPLL). local_gen_map
   marked loop vars gen only when the iterable was a boxed LOCAL —
   `for clause in clauses` with clauses a gen param left `clause`
   unmarked, so callee params (clause_satisfied/unit_lit's `clause`)
   defaulted num. Fix: widen_ptypes seeds each function's caller-locals
   map with its gen/dict params (classes whose elements are boxed;
   buf/tensor deliberately NOT seeded — their elements are numeric and a
   gen mark would route C-double values onto the env path). The memo
   cache key now includes the seed's name list
   (local_gen_map_for_seeded) — seeds grow monotonically with ptypes, so
   a map cached under an earlier smaller seed must not serve a later
   round.

2. **`x[i]` proves indexable, never numeric-element** (t81,
   `non-numeric element in \`watches\`[0] (list)`, --cdcl). The buf class
   assumed numeric elements; EigenMiniSat stores LISTS in lists (watch
   buckets). elem_is_boxed_use (the buf→gen demotion) now also counts
   the element uses that are VM errors on a number: `x[i]` as append's
   in-place TARGET (the appended-VALUE position stays non-evidence —
   numbers are appended all the time), `x[i][j]` and `x[i][j] is v`,
   `for y in x[i]`, `x[i].f` / `x[i].f is v`, and `len of x[i]`.

3. **Dict/tensor callee positions are pass-through evidence** (t82,
   `non-numeric value in a numeric context at cdcl_step: .store (type
   dict)`, --cdcl conflict path). find_gen_param_use counted only "gen"
   positions, so a param whose ONLY use is flowing into a callee's dict
   param (should_compact_deleted_clauses's `store` →
   clause_store_len) stayed num, and the caller emitted `session.store`
   through the numeric dot read. Now gen|dict|tensor positions all
   count.

**Still loud, by design:** returning an element field whole
(`return recs[i].name`) stays runtime-loud — the field's class is
statically unknown, and the numeric-default-plus-checked-read contract
(F-OURO-32) already makes it a sited error, never a silent 0.

**Correctness:** concat-EMS AOT vs the VM running the SAME concat file
(load_file semantics out of the frame): all 7 tests/fixtures + all 8
tests/corpus CNFs x {default DPLL, --cdcl} = 30 runs, stdout byte-exact
(only in-band `ms=` normalized), exit codes equal, zero divergences.

**THE FIRST NATIVE NUMBER** (whole-solver native, no embedded-VM
confound — v0.39.0 pin, --cdcl, n=5 medians, taskset -c 0, SSE2 devbox,
VM = same concat file):

| instance | VM-concat | AOT-concat | VM/AOT |
|---|---|---|---|
| tseitin 3x3 | 1.63 s | 0.31 s | **5.26x** |
| tseitin 3x5 | 13.95 s | 1.74 s | **8.02x** |

(3x3 runs: VM 1.63/1.65/1.71/1.63/1.63, AOT 0.43/0.30/0.31/0.30/0.31;
3x5 runs: VM 13.98/13.95/13.90/14.01/13.91, AOT 1.74 x5. Outputs
byte-exact on every timed run's first sample.) The ratio grows with
instance size as fixed startup amortizes, converging on the measured
8.5x observer-elision ceiling (EigenScript#915) — consistent with the
#86-thread attribution that the bulk of AOT's win on an
assignment-heavy solver is observer elision by construction, with
native codegen taking the residue. Contrast F-OURO-35's
load_file-structured build at **0.93–0.96x** (solver core on the
embedded VM): whole-program native compilation is what unlocked the
number. EigenMiniSat#88's ~8x threshold for reopening the 5x6 ladder
rung is met at 3x5 scale.

Gates: aot/test/run.sh (t80–t82 added) + test/run.sh (57 programs +
bootstrap) green at the v0.39.0 pin; fuzzdiff 40 programs seed 42 → 0
divergences / 0 run-pasts / 0 gaps.

## F-OURO-37 — static `load_file` splicing is sound at the v0.43.0 pin: the VM's chain is file-relative and the splicer mirrors it — FIXED-by-pin + mirror (#147, EigenScript#1056/#1106)

**BUG (upstream, fixed by the pin; AOT mirror landed with the bump).**
`splice_static_loads` (#129) resolves a literal `load_file` target at build
time as `dirname(source) + path`, while the c1684bc VM resolved **cwd
first**. The issue's layout, reproduced exactly (2026-09-06, both rc 0 —
the silent-wrong class):

```
A/inc.eigs  print of "SCRIPTDIR-COPY"    B/inc.eigs  print of "CWD-COPY"
A/prog.eigs load_file of "inc.eigs"
cd B && eigenscript(c1684bc) ../A/prog.eigs   -> CWD-COPY
cd B && ./prog_compiled_from_A (AOT @61f8319) -> SCRIPTDIR-COPY
```

**The rule now (v0.43.0 = a6c50fb, #1106; docs/SPEC.md "Modules",
docs/LANGUAGE_CONTRACT.md "One file, three roads").** `load_file` and
`import` share ONE chain, anchored on the directory of the **file
containing the call** (nested loads use the loaded file's directory; a
function's `eval` uses its defining file's): (1) an absolute path as-is;
(2) `<containing dir>/<path>`, symlinks and `..` canonicalized; (3) the
`eigs_modules` walk — bare `<name>.eigs` only, `<dir>/eigs_modules/<name>/
<name>.eigs` at each level up to the project root; (4) `<project root>/
<path>`, the project root being the nearest ancestor of the containing
directory (itself included) holding `eigs.json` — skipped when none; (5)
the stdlib roots `<exe>/../<path>`, `<exe>/../lib/eigenscript/<path>` (and
lib/-stripped), then `$HOME/.local/lib/eigenscript/` likewise. **There is
no process-cwd step** and no containing-dir-parent fallback (that is why
this repo's own `eigs.json` landed in 61f8319: `aot/compile.eigs` reaches
`src/frontend.eigs` through step 4 now). `import name` requests
`name.eigs` then `lib/name.eigs` through the same chain. Measured on the
pin: `cd B && eigenscript ../A/prog.eigs` prints SCRIPTDIR-COPY from both
cwds.

**Does the splicer match it, case by case?** Before this bump it answered
step 2 only and left everything else to a runtime call into the linked VM,
which resolves from the binary's BAKED `g_script_dir` (F-OURO-34) — the
main program's directory. That is the VM's base for the main file but not
for a spliced child, so:

| case | VM (v0.43.0) | splicer before | now |
|---|---|---|---|
| sibling `inc.eigs` | step 2 | spliced | spliced (`_lf_resolve` step 2) |
| subdir `sub/x.eigs` | step 2 | spliced | spliced |
| child's own sibling (nested load) | child's dir | spliced (base = child path) | spliced |
| project-root-relative from a subdir file | step 4 | **runtime call, interpreted** — dies "undefined variable" if it calls a compiled fn (the #127 class) | spliced (step 4, eigs.json walk) |
| `eigs_modules/<n>/<n>.eigs` | step 3 | runtime call, interpreted | spliced (step 3) |
| absolute literal | step 1 | `dir//abs` unreadable → runtime call | spliced (step 1) |
| stdlib `lib/int_vector.eigs`, main file | step 5 | runtime call (t76) | runtime call, literal as written (the baked base is the VM's; F-OURO-34's tier) |
| stdlib reached from a SPLICED child (`sub/child.eigs` → `lib/bcd.eigs`, main dir holds its own `lib/bcd.eigs`) | step 5 from the child's dir — the main dir's copy is never consulted | runtime call — the linked VM re-runs the chain from the baked MAIN dir, whose **step 2 answers first**: `MAIN-DIR-BCD-SHADOW` then `undefined variable 'from_bcd'` rc 1 (both rc 0 when the child calls nothing — silent wrong) | **literal rewritten to the absolute stdlib path** the child's chain answered (step 1 on both sides; `t307`, arm assertion 4). Round 2: the first cut of this table called the tier "base-independent" — measured false by the blind critic (`min147`: `lib/test.eigs` + `sub/child.eigs` + `prog.eigs`, no eigs.json; VM `child-done` / AOT `PROJECT-LIB-TEST`) |
| stdlib-tier hit the transpiler cannot check (no `$EIGS_DIR`), spliced child | step 5 | runtime call | **refused by name** (`build.sh` always passes `EIGS_DIR`; a hand invocation without it would otherwise guess) |
| nested-POSITION literal in a spliced child (branch/try/function) | resolved from the CHILD's dir at run time | runtime call from the MAIN dir — wrong base | literal rewritten to the absolute path the child's chain answers (step 1 on both sides) |
| unresolvable, main file | io error from the main dir | runtime call → same error | unchanged |
| unresolvable, spliced child | io error from the child's dir | runtime call from the main dir — can resolve where the VM raises | **refused by name** (`aot/test/refuse/lf147_nested_unresolved.eigs`; the VM runs it rc 0 via try/catch) |

`_lf_resolve` (aot/compile.eigs) answers steps 1–4 statically (absolute
base from `getcwd`, `.`/`..` folded; no realpath builtin, so a symlinked
directory's physical parent is the one un-mirrored edge) and step 5
against `G_STDLIB` = `$EIGS_DIR` (== `<exe>/..` for the baked exe dir;
absolutized, since the path it returns is what a spliced child's literal
is rewritten to). The one rule that fell out of round 2: **a runtime
`load_file` call inside the native program is only sound when its base is
the VM's** — at depth 0 (baked `g_script_dir` == the main file's dir) or
when the literal is absolute (step 1). Every other literal in a spliced
child is spliced, rewritten to an absolute path, or refused;
`splice_module` uses the same resolver for `<name>.eigs` then
`lib/<name>.eigs`; `nlb_scan` reads nested targets through it.

**Fixtures.** `aot/test/t306_load_file_project_root.eigs` (+ `test/data/
lf147_*`): sibling, nested-sibling and project-root loads, each proven
SPLICED by the loaded file calling a function defined in the main file
(an interpreted load cannot see it). `aot/test/t307_load_file_stdlib_from_
child.eigs` (round 2): `data/lf147_sub/stdlib_child.eigs` loads
`lib/bcd.eigs` — from its directory the chain ends at the stdlib — while
`aot/test/lib/bcd.eigs` is a decoy at the MAIN file's step 2 that prints
and defines nothing; the child calls `from_bcd`. `aot/test/load_file_
shadow.sh`, an arm of `aot/test/run.sh`: the issue's A/B layout under a
temp dir with the subdir and project-root steps shadowed too, four
assertions — the shadow is live on the VM (a B/control.eigs prints
CWD-COPY), the VM from B prints the A copies, the binary from B is
byte-identical, and (4, round 2) the critic's exact no-eigs.json layout
(`C/lib/test.eigs` decoy, `C/sub/child.eigs` → `lib/test.eigs` +
`assert_eq`, run from C itself) is byte-identical VM vs AOT with the decoy
proven live by `C/control.eigs`. Planted: the splicer made cwd-first
(`getcwd + path` tried before step 2) → the arm goes red on assertion 3
(AOT prints `CWD-COPY|CWD-SUB|undefined variable 'helper'` — it spliced
B's copies, which define nothing); the shadow deleted → red on assertion
1; the depth>0 stdlib rewrite removed (round 2's fix) → `t307` FAILs
(`MAIN-DIR-BCD-SHADOW` / `undefined variable 'from_bcd'` rc 1 vs the VM's
`26` rc 0) and the arm goes red on assertion 4 by name; the C decoy not
written → red on assertion 4's control. Four more plants, each red by name: the frontend's `report`
lexer arm reverted → `report is 5` accepted by the self-host driver (the
reject tier's case goes red); the codegen `report_value` arm removed →
`report_reserved_forms.eigs` dies "undefined variable 'report_value'";
the `for` exemption restored → `for_body_fresh_binding.eigs` ACCEPTED; the
depth>0 refusal removed → `lf147_nested_unresolved.eigs` ACCEPTED.

**Bump fallout mirrored in the same commit (the deferred-mirror rule):**

- **#1110 / EigenScript#1102 — `report` and `report_value` are reserved
  observer forms (E005).** `src/frontend.eigs` lexes them as their own
  token kind and mirrors every `p_report_error` arm of parser.c: any
  binding position (assign, compound, `local`, `define` name and params,
  lambda params, `for`/listcomp binder, `catch` name, destructuring,
  `import`), any value use, and a non-identifier operand (`report of
  (x + 0)`, `report of d.a`, `report of 5`, `report of converged`); still
  admitted: `report of (x)` (parens return the ident node, as in C), the
  soft-keyword identifier fallbacks as operands, and `d.report` as a dot
  key. Probed on the oracle: 20 programs, rc agrees on all 20 for both the
  self-host driver and the AOT transpiler. The frontend's own
  `_env_set_local of [env, "report", report]` was the first casualty —
  every self-host program and every AOT build died on it. The evaluator's
  call arm now refuses the two forms by name (it keeps no history).
  Pinned by 16 `reject_one` cases in test/run.sh; the
  `observer_report_shadowed.eigs` matched-bug canary (`define report(v)`
  half-shadow, EigenScript#1102) flipped as designed and became one of
  them. New parity program `test/programs/report_reserved_forms.eigs`.
- **Self-host codegen had no `report_value of <ident>` arm** (fell to a
  plain call → "undefined variable 'report_value'": the form is an opcode
  pair with no builtin behind it). Latent while no parity program used it;
  the pin made it the only way to write the word. Mirrored from
  compile_node_inner (OP_REPORT_VALUE_SLOT/NAME = 89/90), and
  `trajectory of <ident>` alongside (91/92), same shape.
- **#1106's for-body scoping on the import road.** Self-host codegen: no
  mirror needed — it compiles the main road only (`import` is the VM's
  OP), and for the main road compiler.c's new `lev_has` arm still emits
  OP_SET_NAME. AOT: t100 went red — the VM's `keys of M` now carries
  `floop` from a module-level `for qv in range of 2: floop is 5`, the AOT's
  static dict did not (`M.floop` null, a wrong VALUE). Measured on the pin
  (`a is 1 / <body> / b is 2` imported, `keys of M`): a fresh `is` in a
  `for` body, in a nested `for`, and in an `if` inside a `for` all reach
  the module dict; only the loop BINDER stays loop-scoped (`i is 9` in the
  body updates the loop-local, `M.i` absent); and the key is ABSENT when
  the body did not run (`for i in []:`, a `break` before the assignment).
  So a `for` body is an `if` body for the snapshot: `collect_cond_binds`'
  measured-table exemption ("a `for` body has its own env") — the c1684bc
  row the pin flipped, invariant 16's axis case — is dropped; a fresh
  for-body binding is refused like a fresh `if` binding (#141's message),
  with the binder excluded via a copy of `bound`. t100's `floop` shape
  became `test/refuse/for_body_fresh_binding.eigs` (the VM runs it rc 0,
  `["a", "floop", "b"]`); `_t100mod` keeps the accepted forms (re-assign
  of a top-level name, write to the binder) and t100 prints `M.qv` (null on
  both sides). t95–t99 unchanged.
- `build.sh` gained `AOT_TRANSPILE_CWD` (paths absolutized, transpiler run
  from that directory) so the shadow arm can drive the transpiler from B;
  the default path is unchanged.
- **#1113** (embed observer gate default): no mirror.
- Pin recorded in two places (`.devcontainer/Dockerfile` `EIGS_REF`,
  `.github/workflows/aot-avx2-bench.yml`'s checkout SHA — invariant 30).

Gates at a6c50fb (`EIGS=<v0.43.0 checkout>/src/eigenscript
EIGS_DIR=<v0.43.0 checkout>`): `bash test/run.sh` → `ALL PASSED (63
programs + bootstrap)` (`PASS: bootstrap fixed point (and the
self-compiled program runs)` — the fixed point holds with the frontend
change); `bash aot/test/run.sh` → 369 PASS lines, `PASS: load_file_shadow
(A/B layout from the shadowing cwd, 3 assertions)`, `--- bench tier: 12
program(s) build-checked ---`, `--- refusal tier: 56 guard(s) exercised
---`, `--- runtime-refusal tier: 1 residual(s) exercised ---`, `--- all
AOT parity tests passed ---`; `aot/core_check.sh` → `build.sh CORE matches
upstream SOURCES minus CLI_ONLY (20 TUs)`.

## F-OURO-38 — #139 census closure: 5 of the original 9 compile at HEAD, the temp-pool drain freed a caller's argument under a nested call — FIXED; the v0.43.0 oracle cannot run the compiler at all (pin, #147)

**The instrument, run three ways (HEAD 61f8319, 2026-09-06).**

1. **Against the v0.43.0 release oracle, as the brief asked:
   `transpiles: 0 / refused: 116`** — every row the same first refusal,
   `Parse error line 2300:39: 'report' is a reserved observer form ...
   [E005]` in `src/frontend.eigs` (`_env_set_local of [env, "report",
   report]`). That is the COMPILER failing to parse, not the consumers:
   EigenScript#1102 (v0.43.0) reserved `report`/`report_value`, and the
   frontend binds the word. Nothing about the envelope can be measured on
   that oracle until the pin moves (#147, in flight); the number is
   recorded so nobody reads a 0/116 as an envelope collapse.
2. **Against the PINNED oracle c1684bc (the `EIGS_REF` in
   `.devcontainer/Dockerfile`, 60 s transpile budget):
   `transpiles: 85 / refused: 29 / timeout: 2 (total 116)`.** The total
   is 116, not the 120 of the 2026-09-05 rows, because this box has no
   `legibility-experiment` checkout; the discovery roots are the same.
3. **The two timeouts re-run with a 600 s budget:** `EigenMiniSat/
   minisat.eigs` transpiles (41 s alone, >60 s under a loaded box);
   `Tidepool/tidepool.eigs` is a REFUSAL after 122 s (`function
   'predators_for_tier' assigns module name 'n' whose first module-level
   binding does not provably precede` — the order guard). So the honest
   split is **86 transpile / 30 refused / 0 timeout**.

First-refusal histogram over the 30 (messages normalized):

| n | first refusal |
|---|---|
| 10 | function assigns module name whose first module-level binding is later (order guard; Tidepool x7 incl. tidepool.eigs, dynamics/life, train) |
| 9 | task_spawn — cooperative tasks are pumped by the VM run loop (#188, design; liferaft x4, eddy x4, cross_lab) |
| 4 | `local X` shadowing a module binding is not supported here (tidelog, dynamics/orbit, predicate_calibration, predicate_fit) |
| 2 | nested define assigns enclosing name (outward; token_train, phugoid/swarm) |
| 2 | cannot emit statement node (`import` in module_scope_lab, `binop` in transformer_eval_sequence_v2) |
| 1 | zero-arg call binds the parameter null and the callee reads it (polymethod) |
| 1 | a local of an OBSERVED function (gauntlet) |
| 1 | temporal interrogative without `at` only supports `prev` (observer_lab) |

**The original nine, BUILT (transpile + gcc link) and RUN against the
c1684bc VM** — "compiles" here means the binary links; the run column is
the program's own headless mode:

| program | build | run vs VM |
|---|---|---|
| DMG/dmg.eigs | COMPILES (41 s) | `roms/cpu_instrs.gb --cycles 500000`: byte-exact modulo the two wall-clock lines (`Time:`/`Speed:`), rc 0 |
| EigenMiniSat/minisat.eigs | COMPILES (71 s) | `--cdcl simple_sat.cnf`, `simple_sat.cnf` (DPLL), `--cdcl pigeonhole_3_2.cnf`: byte-exact modulo `ms=`, rc 0 — every counter identical |
| EigenRegex/regex.eigs | COMPILES (4 s) | a package: standalone prints nothing both sides (rc 0). Driven through `import regex` by its own stage suites (copied beside the package so the AOT's beside-the-program import resolution finds it): **s1 (20 checks) and s8 (48 checks) DIED rc 1 on the first check** before this entry, byte-exact after — see the bug below |
| liferaft/liferaft.eigs | REFUSED | `task_spawn` (#188, design) |
| tidelog/tidelog.eigs | REFUSED | `` `local buf` shadowing a module binding `` (buffer-classified shadow) |
| dynamics/dynamics.eigs | COMPILES (1 s) | a package: prints nothing both sides (rc 0); `dynamics/solve.eigs` byte-exact (14 lines), `logistic.eigs` byte-exact |
| eddy/explorer.eigs | COMPILES (1 s) | a module fragment (loaded by explorer_main, which is refused on `task_spawn`): standalone defines only, prints nothing both sides (rc 0); no headless driver reaches it under the AOT |
| polymethod/polymethod.eigs | REFUSED | zero-arg call to `main` — the callee reads its parameter (`main()` then `local n is 12` inside a branch, so the implicit `n` is not provably dead) |
| Tidepool/eval_policy.eigs | REFUSED | `_unflatten_weights` assigns module name `policy` (order guard) |

**5 of 9 compile** (DMG, EigenMiniSat, EigenRegex, dynamics, eddy);
newly admitted since the issue's first table: EigenMiniSat, EigenRegex,
dynamics, eddy. Consumer-derived fixtures per admitted program:
EigenMiniSat t77/t80/t81/t82/t108/t210; EigenRegex t187 (its callback
convention) and now **t309**; dynamics t303/t304/t305; eddy had NONE —
**t308** added (its `on_scrub` shape: a two-parameter callback stored into
a widget field the library initialised null, fired through the field
behind a `!= null` guard, mutating module dict state). Exit condition
(≥5 of 9 compile AND a fixture per admitted program): **MET**.

**The bug the census paid for (BUG, FIXED here, t309).** Round 171 made
every statement that owns argument temporaries end with
`aot_tmp_drain()` — the WHOLE pool. A user call nested in an argument
list (`check of ["a matches a", regex.re_match of [prog, "a"], 1]`)
evaluates its sequenced arguments into the pool (`_sqa0` = the label)
and then runs the callee, whose statements drain the pool under the
caller: the label is freed, its memory reused, and `label + " OK"` dies
`cannot apply '+' to ? and str` (rc 1) where the VM prints — or, with a
luckier allocator, prints garbage. 14-line repro
(`check of ["x", g of 1, 1]` where g's body makes any call with a
list-literal argument). Fix: drains are RELATIVE — `emit_stmts` declares
`int _tmsN = aot_tmp_mark();` before a temp-owning statement and drains
to it after; `emit_return` drains to the same mark. The per-iteration
release in loops is unchanged (each body statement has its own mark),
and a callee's leftovers are released by the caller's own drain-to.
Planted: reverting the two drain sites in place turns t309 red (rc 1,
the original message); emitting `make_null()` for a function-as-value
turns t308 red (the callback never fires).

**Residuals.** (a) `import` resolves beside the program then the stdlib
only — EigenRegex's real suites (`tests/test_s1_literals.eigs` importing
the package at the repo root) refuse at build time, `import 'regex' not
found`; the VM at c1684bc resolves via cwd and at v0.43.0 via the
`eigs.json` project root (EigenScript#1056) — the pin bump is where that
road should be matched. (b) The census counts acceptance, not runs: this
entry is the second time a "compiles" row died on its first real driver
(F-OURO-36 was the first) — `CENSUS_BUILD=1` plus a per-repo headless
command is the next instrument. (c) The order guard is now the largest
class (10), all Tidepool-shaped (`new_game` assigning `game`); #218 lifts
one form of it.

Gates at c1684bc: aot/test/run.sh and test/run.sh — see the commit body.

## F-OURO-39 — the all-or-nothing `g_observed` gate is the larger half of the AOT's observer overhead; per-name gating is sound and measured, not landed — GAP/CONSTRAINT (#126; recorded, not scheduled)

Ledger of record for ouroboros#126 (a measurement, not a defect: no
silent-wrong is involved — every observed program is byte-exact today,
it is only slower than it needs to be). Verified against HEAD 61f8319 on
2026-09-06; re-RAN vs re-READ is marked per item.

**The finding.** One observer read anywhere in a unit sets `g_observed`
for the WHOLE unit, and under it every module-scope scalar is demoted
from a C variable to a name-keyed env slot: a loop counter nobody ever
asks about becomes `aot_observe_num(__eigs_g, "i", …)` on write and
`aot_get_num_named_ic(__eigs_g, "i", …)` on every read — a hash lookup
plus a type check per access. `unobserved:` is the WRONG lever for this
cost: it makes `aot_observe_num` return early from the entropy walk, but
the name-keyed store and lookup are already emitted (upstream it is the
right lever — 8.51x on EigenMiniSat, EigenScript#915 — because there the
cost IS the walk).

**Executed evidence — the bench triple** (`aot/bench/obs_gate_a_unobserved`
/ `_b_gate_only` / `_c_full.eigs`, committed by #125: identical
arithmetic, ranged-dot kernel D=256 M=512 x30; A no observer, B one
`report` at the end with the hot loops inside `unobserved:`, C the same
without the wrap; `aot/test/run.sh`'s bench tier build-checks them,
run.sh:161-184). The invariant that makes the arms comparable is that
all three print the same checksum — re-RAN: the three AOT binaries
print `checksum 0.018599999999997712`, the three VM runs
`0.018600000000004294`; the arms agree with each other (the invariant),
and the AOT-vs-VM tail difference is the `dot` lane-reassociation
tolerance class (`_tol`, by the dot spec; build.sh is `-march=native`
and this host is AVX-512), not a divergence.

| arm | at filing 2026-08-28 (dev box, median) | round-191 tree 2026-09-05 (dev box, n=5 sorted) | per-name DRAFT (dev box) | HEAD 61f8319, 2026-09-06, THIS host, two n=5 runs (sorted) |
|---|---|---|---|---|
| A unobserved | 28 | 27 27 **28** 32 42 | 27 29 **31** 32 34 | 13 13 **14** 14 14 / 13 13 **13** 13 14 |
| B gate only | 85 | 49 49 **51** 54 55 | 34 34 **38** 39 40 | 22 23 **23** 24 36 / 22 23 **23** 23 23 |
| C full | 102 | 67 69 **70** 70 105 | 34 35 **36** 36 41 | 29 30 **30** 31 32 / 30 31 **31** 31 33 |

The dev-box columns are re-READ from the issue (the per-name draft is
not in the tree). The last column is re-RAN here: Xeon @ 2.80 GHz
(AVX-512), 4 vCPUs shared with ~5 other agents (load ~4.6), pinned
c1684bc oracle as `EIGS`/`EIGS_DIR`; absolute ms are this host's and
NOT comparable to the dev box, the ratios are. Gate share
(B−A)/(C−A): 57/74 = **77%** at filing; 23/42 = 55% on the round-191
tree; 9–10/16–17 = **56–59%** at HEAD here. B = 1.8x A (round 191),
1.7x A (HEAD here). Rounds 126→191 cut the gate from 57 ms to 23 ms on
the dev box; it is still the larger half of the overhead. VM on the same
kernel here, n=5 sorted: A 51 51 52 52 52, B 46 48 48 48 48, C 55 55 56
56 57 — so the AOT is 3.8x the VM unobserved and only ~2x observed; the
gate is where the AOT's own multiplier goes.

**Mechanism, re-RAN in the generated C** (compile.eigs on the two arms;
`gen_a.c` / `gen_b.c`): arm A carries 0 `aot_observe_num` and 0
name-keyed reads; arm B carries 12 `aot_observe_num` stores and 23
`aot_get_num_named_ic` reads, the module loop `i` among them
(`aot_observe_num(__eigs_g, "i", 0)` then every read of `i` in the
loop condition and the two buffer writes a named lookup). `aot_dot_range`
(the SIMD dot) is present once in BOTH — the gate is not about
vectorised builtins.

**Why per-name gating is SOUND: verdicts are suffix-determined.** The
issue's second comment measured four trajectory shapes over 60
assignments (converging / oscillating / diverging / drift) against
suffixes 11, 12, 15 of the same sequence, all six predicates plus
`report`: byte-identical on every verdict — **11 assignments suffice**
(the `OBSERVER_WINDOW_N` window plus one seed). That table is re-READ.
The source-level reason is re-RAN against the v0.43.0 runtime
(`/home/user/wt/eigs-pin/src`): `OBSERVER_WINDOW_N` is 10
(eigenscript.h:361); the six classifiers
`observer_slot_{converged,equilibrium,improving,diverging,oscillating,stable}`
(eigenscript.c:965–1031) contain **zero** references to `obs_age`, the
slot's only cumulative field; `obs_age` is read only as the
first-observation test (eigenscript.c:592) and is SYNTHESISED from the
window counts by `observer_slot_from_trajectory` (eigenscript.c:1208,
the comment at 1262: "a full slot re-fed through the classifiers needs
obs_age > 0 so the partial-window fallbacks behave like a live slot's")
— the runtime already contains, as working code, the proof that a
classifiable slot is reconstructible from bounded state. Consequences
for an untraced program: (1) unqueried slots need NO maintenance — that
is the gate cost, and those are the loop counters; (2) queried slots
need only their last 11 assignments, and the naive split (keep today's
machinery for queried names, plain C locals for the rest) captures
essentially the whole win because the queried slot in the kernel takes
2 assignments while the counters take millions. This costs no
exactness: values stay byte-exact, verdicts are what programs print, so
a verdict-exact AOT is byte-identical at the output and the existing
differential validates it unchanged. `g_traced` stays a SEPARATE gate
(per-assignment entropy is genuinely observable on the tape). The
enabler is #125's NAMED predicate form: a bare predicate reads
`g_last_obs_slot_*`, a runtime alias that can reach any slot, so only
`diverging of a` / `report of x` give a statically knowable query set.

**The per-name draft (2026-09-05, not landed) and what blocked it.** A
program-wide census of every name an observer read can name
(predicate / report / report_value / trajectory / observe operands,
every interrogative) with an ALL fallback for a bare predicate, a
non-ident operand, or any builtin that runs interpreted code (#1027),
applied as `obs_name(n)` at the module declaration, the numeric read
and the four observe-on-write sites: module-level observer fixtures
matched (t27_observer, t108_genp_rebind_c_name,
t119_interrogated_set_per_scope, t218_interrogated_binder_kinds,
t244_param_binder_string_list — all present at HEAD, re-RAN `ls`) and
three new fixtures covered the fallbacks (draft numbers t297 per-name,
t298 bare-predicate ALL, t299 eval ALL — NOT in the tree; t297–t299
are still free but t300–t305 have since been taken, so the draft's
fixtures must be renumbered on landing). It BROKE the FUNCTION-scope
storage model: a function's numeric map is seeded from the module map
only when the program is unobserved (`emit_function`, compile.eigs:8590;
the `g_mod_observed == 1 and g_observed == 0` seed at 8657), so a
function reading a module numeric fell to the env-read arm
(`total is total + 1` inside `check`: `undefined variable 'total'` in
test_entropy_reference_stop / test_entropy_types / test_observer_slots),
and a boxed call argument in an observed function read its local from
the env (t107 `g of m`; t202 `limit`). The whole-program flag is
consulted on 61 lines of compile.eigs at HEAD (`grep -c g_observed`;
the draft counted ~40 sites — same job, the tree has grown). The
per-name rule has to be threaded, each site classified name-specific vs
genuinely global, through: the function seeding (emit_function /
`g_mod_observed`), the boxed read arms, boundness (`G_BOUND_SO_FAR`,
3160), the loop-binder kinds (the loop-scoped shadow arm, ~3938) and
the outward-write regime (`g_outward_obs`, 3291/3365 — the
aot-differential skill's invariant 14: an effect lands in the regime of
the NAME it targets), with
fixtures across module / function / outward-write scopes. That is its
own round.

**Pinned today by:** the bench tier (all three arms must build), and
the observed-program fixtures above plus t305 (per-call fresh slot,
#217) for the contract any per-name rule must keep byte-exact.

**Status: recorded, not scheduled.** Reopen as an issue when the
threading round is picked up; the draft, its fixtures and the numbers
above are the starting point.

## F-OURO-40 — struct lowering of statically-shaped dicts: re-scoped from "~2.8x, the gap to real-time" to ~10% of DMG's runtime; do the numeric-dispatch calling convention first — BY-DESIGN / not-now (#133; recorded, not scheduled)

Ledger of record for ouroboros#133 (split from #130). Nothing here is a
divergence: field access through the inline caches is byte-exact and
gated (t88_dispatch, t92_field_fastpath_hot). It is a sizing record so
the next session does not start from the premise in the old title.

**The estimate history — the finding worth keeping.** #130's title
claimed struct lowering was worth ~2.8x and was "the remaining gap to
Game Boy real-time". Both halves were wrong: the gap was closed WITHOUT
it (ouroboros#129 stopped interpreting loaded modules, #130 added inline
caches on dict fields and env names, unboxed comparison against a
numeric operand, borrowed field/index access and a stack argument vector
for `dispatch` — DMG at 135.8% of hardware, AOT 5.6977 MHz vs VM 1.3010
MHz, `cpu_instrs.gb --cycles 1500000` n=7, dev box, re-READ from
DMG/CLAUDE.md); and the ~2.8x came from SUMMING the profile's dict-access
rows, which assumes the cost is the OPERATION when it was the LOOKUP —
far cheaper to remove. #133's body then sized the residue at ~13% (the
three field helpers, fully cached: `aot_dot_get_tb_ic` 5.8%,
`aot_dot_set_num_tb_ic` 4.7%, `aot_dot_num_tb_ic` 3.5%). PR #137 took the
cheap half and re-measured: the three helpers plus `aot_index_get_ib`
were 20.8% across 968 call sites, every one a plain out-of-line `static`
— a real call for what is, on a hit, a bounds check, a pointer compare
and an array index. Splitting each into an inlinable fast path plus a
`noinline` slow path (semantics unchanged by construction: every non-hit
case falls through to the untouched original) bought **1.062x** and cut
the helpers 16.7% → **9.8%**; boxing + refcount (`make_num` /
`free_value` / `val_*`) went 20.9% → 27.4% of the remainder;
machinery:work 4.36:1 → 3.87:1. So struct lowering now targets ~10% of
runtime directly plus whatever share of the boxing is attributable to
dict-held numbers: best case ~1.1–1.15x. (Profile percentages re-READ
from the #133 thread; they need the DMG ROM run under `perf` on the dev
box to re-run.)

**Verified at HEAD (re-RAN):** the #137 split is in the tree —
`aot/aot_rt.h:424–481` holds the four `static inline` fast paths
(`aot_dot_get_tb_ic`, `aot_dot_num_tb_ic`, `aot_dot_set_num_tb_ic`,
`aot_index_get_ib`), their slow paths are `__attribute__((noinline))` at
1923 / 2352 / 2366 / 2381 (merge 70f78bf). DMG transpiled at HEAD with
the pinned c1684bc compiler (`compile.eigs /home/user/DMG/dmg.eigs`, rc 0,
27 s, 8,163 lines of C): `aot_dot_num_tb_ic` 352 sites,
`aot_dot_set_num_tb_ic` 248, `aot_dot_get_tb_ic` 24, `aot_index_get_ib`
49 — 673 in total, against #137's 968 on its own tree (rounds 138–191
rewrote element and field access — round 170's borrowed field/element
arguments and numeric element class among them — so this is the tree's
count, not a measured reduction of the same sites). The generated C also
shows the shape the lowering would target: 805 function-static
`AotNameIC` caches and 898 dict-field IC pairs (`static int __icN = -1;
static const char *__ickN`).

**Why the boxing is not dict-shaped** (attributed with a frame-pointer
build after three guesses were wrong — re-READ): `run_headless_loop`
directly 2.47%, opcode-handler wrappers via `aot_dispatch` 2.46%,
`fetch8` 1.20%, `handle_interrupts` 0.95%; no dominant source, and the
largest single one is `__wrap__op_*` boxing a `double` return the caller
immediately unboxes — which a **numeric-dispatch calling convention**
fixes, not struct lowering. Hypotheses killed by counting before any
code: interning literal constants (268 of 523 `make_num(` sites are
literals, only 18 in hot functions); `mem.data[addr]` boxing dominating
(45 static sites index a buffer through a dict field; most
`aot_index_get_ib` calls index a LIST to reach a dict, `ctx[0].pc`, and
allocate nothing); box-then-collapse on assignment
(`aot_lv_set(&slot, make_num(...))` appears 0 times — #132's
numeric-write inference already catches those).

**Recommendation, unchanged:** size any struct-lowering work against the
~10%, not the old estimate, and do the numeric-dispatch calling
convention first — smaller, safer, and it targets the larger measured
cost. This is the second time on this axis that an estimate built by
summing profile rows came out wrong; the next one needs a
frame-pointer attribution before a design.

**What a SOUND bail-out would need** (the hard part is the bail-out, not
the lowering — a dict is a first-class value and the failure mode is a
silent wrong number, the class this repo exists to eliminate): a
whole-program escape analysis that refuses lowering for any dict that
is printed or formatted, passed to a builtin, returned or stored into a
generic context (a list element, another dict's field, a function
value's capture), indexed by a computed string, aliased through a
container (`ctx[0].pc` is the common DMG shape), compared or hashed by
identity, or can gain, lose or retype a key at runtime — and it must be
conservative in the direction of NOT lowering, with a differential
fixture per escape class (the F-OURO-32 rule: a refused shape must be
loud, never coerced). Without that analysis in hand the lowering is not
buildable safely; with it, the ceiling is the ~10% above.

**Status: recorded, not scheduled.** Reopen as an issue when the
numeric-dispatch convention has landed and the residue is re-measured;
if the helpers are still ~10% then, this entry is the sizing.

## F-OURO-41 — concurrency under the AOT: `task_spawn` has no run loop, `spawn` races the name-keyed observer env, compiled threads share emitter globals — CONSTRAINT (#188; every arm loud, design halves recorded, not scheduled)

Ledger of record for ouroboros#188 (blind-critic round 108). One root —
concurrency builtins were admitted through generic dispatch with no
execution basis — three arms. Every silent-wrong the issue describes is
now LOUD (build-time refusal, named runtime death, or a NONDET ledger row
excluded from both sides of the corpus comparison); what remains is
design work. Verified at HEAD 61f8319, 2026-09-06, against the pinned
c1684bc oracle; each item marked re-RAN or re-READ.

**Arm A — `task_spawn`: the task never runs.** `task_spawn` accepts the
AOT's `make_builtin` wrapper and enqueues the task, but the cooperative
scheduler is pumped only by the VM dispatch loop (vm.c `CASE(CALL)`:
`g_task_suspend_request` honoured at `base_frame == 0` →
`vm_suspend_halt`); `task_join` sets the request and returns its
placeholder `make_null()`, and native `main()` is plain C with no run
loop. Same root for the `task_sleep` racers (VM `["a", "b"]`, AOT `[]`)
and `task_recv` (VM `hello`, AOT `null`). Shipped: (1) round 108's
BUILD-TIME refusal by name — `mentions_ident of [ast, "task_spawn"]`
(compile.eigs:11134; the program-wide rationale at 427–432: a function
value can flow to `task_spawn` through any binding, so scoping the
predicate to the call's bare argument would breed an invariant-20 hole);
(2) round 189's BOOT REBIND for the seam the AST walk cannot see —
`aot_no_loop_rebinds` (aot_rt.h:2181, called from `main` at
compile.eigs:11700) binds `task_spawn` in the global env to
`aot_task_spawn_no_loop`, which raises `AOT: task_spawn -- no run loop …
(ouroboros#188)` at the call line, the same shape the VM uses for
sandbox-blocked builtins. A computed-path `load_file` is refused at build
time since #127 (`aot/test/refuse/load_file_computed_path.eigs`), so the
residual shapes are an `eval` string and a literal `load_file` the
static pass cannot resolve (test_supervise: `load_file of
"lib/supervise.eigs"` from `tests/`, still a DIVERGE row, but its binary
now dies naming this instead of `cannot suspend … nested evaluation`).
Re-RAN the issue's repro (`define worker(x) … w is task_spawn of
[worker, 21] / print of task_join of w`): VM `42` rc 0; AOT build
refused with the round-108 text. Re-RAN the rtrefuse fixture
(`aot/test/rtrefuse/task_spawn_runtime_load.eigs`, the eval shape): VM
prints `spawned` rc 0; the binary BUILDS and dies `Error line 3: AOT:
task_spawn -- no run loop: …` rc 1 (the run.sh arm at 271–300 requires
exactly that: VM rc 0, AOT builds, binary nonzero with the `# EXPECT:`
text; rc 0 fails as "still silent"; planted at round 189 by removing the
rebind line — the binary printed `spawned` at rc 0).
*To lift:* a native task substrate. A compiled worker is a C function,
so it cannot be suspended at `task_sleep` / `task_recv` without a
coroutine mechanism (ucontext, per-task C stacks, or threads with a
baton). That is a runtime design decision, not a patch — and the VM's
scheduler contract ("a task suspends only at base_frame 0, never inside
a nested evaluation") is itself a VM-shaped rule worth questioning
before mirroring (CLAUDE.md: match the VM by default, but a refusal
forced by a rule that is not a property of the source is a LANGUAGE
finding).

**Arm B — `spawn` with an observed/temporal function: SIGSEGV rc 139 or
silent wrong counts.** Part 2a routes every local of an observed
function through the PROCESS-GLOBAL env by name
(`aot_observe_num(__eigs_g, "seen", …)`, `aot_set(__eigs_g, "i", …)`);
two OS threads race one slot while first-observation grows the obs
array: `undefined variable 'seen'` then rc 139 where the VM prints
`4000 / 4000`, and LOWER contention is worse — an unobserved worker
calling an observed helper printed `6397 / 6223` at rc 0. The axis is
exactly `func_observed` (compile.eigs:5933): unobserved workers, traced
programs, and module-scope observation while workers run are byte-exact.
Shipped: round 108's program-wide refusal — `spawn` mentioned anywhere
AND any function in the unit observed (compile.eigs:11145–11150, keyed on
any observed function, not on `spawn`'s argument, because the function
value can flow dynamically). Pinned by
`aot/test/refuse/spawn_with_observed_fn.eigs` (the direct shape) and
`spawn_observed_helper.eigs` (the unobserved-worker-calls-observed-helper
shape). Re-RAN the issue's repro: VM `4000 / 4000` rc 0; AOT refused
naming `'reader'`. Re-RAN the control (same program, no `report`): VM
and AOT both `4000 / 4000` rc 0, byte-exact — the refusal is on the
axis and not wider.
*To lift:* per-call envs for observed functions — the observer slot must
be FRAME-owned, not name-keyed on the global env. This is the same Part
2a design gap #123/#124 (locals leaking into module scope), #126 /
F-OURO-39 (the 3x gate) and #217 (round 196, t305: a function's observed
locals now get `aot_obs_reset_name(__eigs_g, …)` at entry so the slot is
fresh per call — a reset ON the global slot, which is exactly what two
threads cannot share) all name from different sides. F-OURO-32/35's
per-call env `__eigs_l` already exists for boxed locals; the observer
store has to move with it. Inherited design, pre-v1 — question it rather
than design around it.

**Arm C — compiled threads share mutable emitter globals**
(test_spawn_parallel). Corpus round 170 (1eabb16): the program built
under the AOT and re-run three times against the VM gave mismatch (w9
1719 for 1675, w10 1775 for 1725; 7/12 passed), SIGSEGV, mismatch — it
had matched once inside the gate by luck. Its ledger row is **NONDET**
(`aot/test/canary/corpus_expected.txt:56`, re-RAN `grep`; test_spawn_gc
at :55 carries the same class), a class `aot/corpus_diff.sh` (68–105)
excludes from BOTH sides of the comparison so a lucky run cannot read as
an improvement and the kept observed set carries the row forward
(8dc140f). Root: spawned compiled code shares the emitter's per-program
statics — the inline caches (`static AotNameIC __nNN` and the dict-field
`ic`/`ick` pairs are function-static in the generated C: 805 and 898 of
them in DMG's unit at HEAD), the dispatch tables, and
`g_trace_current_line`. Rule already in force: any NEW per-program
runtime state is thread-local from day one — round 171's temporary stack
is `__thread` (aot_rt.h:2017, re-RAN). *To lift:* make the existing
emitter globals thread-local or per-thread-arena (the ICs are the bulk:
a `__thread` IC costs a TLS indirection on every cached access, so this
wants measuring on the DMG canary before it is the default), then the
row leaves the ledger when the outcome is MATCH on every run.

**Corpus rows at HEAD (re-RAN):** test_task_osr REFUSE,
test_task_sleep_order REFUSE, test_obs_mt_race REFUSE, test_supervise
DIVERGE (named death, above), test_spawn_parallel NONDET, test_spawn_gc
NONDET. Reach beyond tests (re-READ): `lib/supervise.eigs`,
`lib/sync.eigs`, `lib/concurrent.eigs`, liferaft's `cluster.eigs`, eddy's
`dst_core` / `txn_dst` / `rw_dst`, EigenGauntlet's `concurrent_lab` /
`cross_lab`, EigenOS demos, three `examples/task_*.eigs` — every
task-using consumer previously compiled to a binary whose tasks silently
never executed; they now refuse loudly.

**Status: recorded, not scheduled.** Three separate design decisions
(task substrate; frame-owned observer slots; thread-local emitter state),
each reopenable as its own issue when picked up. The refusals and the
rtrefuse arm are the contract until then — a lift must delete the
refusal it replaces, not widen it.

---

## F-OURO-42 — a module NUMERIC name a function both assigns and interrogates is an interrogated call-local (OP_SET_FN_NAME_LOCAL's frame binding), no longer a refusal — FIXED for the numeric class; CONSTRAINT for the boxed / observed / nested-reference members (#218)

**The VM's rule (v0.43.0, `compiler.c` `emit_assign_for_tos` +
`scan_for_interrogated`).** Inside a function, a plain assign to a name
the SAME function interrogates (`prev of`, `what/when is .. at`, the
observer interrogatives) is never slot-eligible and never outward: it
compiles to `OP_SET_FN_NAME_LOCAL`, a fresh binding in the frame env. So
the write never reaches a same-named module binding, a read BEFORE the
first write in the call still walks the chain to the module binding, the
frame binding dies with the call, and `prev of` / `what is .. at` read the
process-global name-keyed tape, module writes included. Round 197 refused
the whole class by name (silent-wrong before that: the AOT wrote the
module static). Measured on the pin, every row rc 0 unless said:

| shape | VM | AOT before | AOT now |
|---|---|---|---|
| the issue's program: `z is 1.0`; `u`: `z is 0.25 / return prev of z`; `u2` same without the interrogative | `1 1 0.25 0.25` | build refusal (round 197); silent `1 0.25 0.25 0.25` before it | `1 1 0.25 0.25` (t310) |
| read / `prev of` before the write, then after; a sibling fn reading `z` during the call; second call (module `z` written twice: 1.0 then 2.0) | `2 1 0.25 2 2 2` on call one (the pre-write read and the sibling both see the module 2, the tape's `prev` is 1 then 2), module still 2, then `2 2 0.25 2 0.25 2` | refusal | match (t311) |
| write in an if/else arm, in a `loop while` body, compound `+=`, an int-class module name (`m is 1`), `z is z + 1` (reads the module 1 each call) | call-local each time, module untouched | refusal | match (t312) |
| for-binder over the name (round 182 loop scope) with a plain write before / after / only inside the loop | binder loop-scoped; post-loop read = the call-local if written, else the module; `prev of z` = last binder value | refusal (only the binder-only form was excluded) | match (t313) |
| `what is z at L` / `when is z at L` from the function and from module scope | tape reads see the call-local's writes | refusal | match (t314) |
| module-OBSERVED program (`report of z` / `stable of z` at module scope), non-observing function | the module slot's answers are those of the control without the writes; module value unchanged | refusal | match (t315) |
| module `z` conditionally UNBOUND (a boundness bit); `u` writes and reads `prev of z` twice; module read after | `null`, `0.25`, then `undefined variable 'z'` rc 1 | refusal | match incl. the death (t316, `_err`) |
| recursion (`z is n` then `print of (u of (n - 1))` inside `u`, `u of 2`) | each frame its own `z`; the tape carries 1.0/2/1/0 so every `prev of z` answers 1 and the module stays 1 (`1 1 1 1`) | Part 2a's own refusal ("observed/temporal function calls another observed/temporal function") | unchanged — that refusal predates this round and is not this class; the pair itself would be exact, C stack storage gives a frame its own copy |

**Mechanism.** `g_cur_interloc` (per function; `aot/compile.eigs`): a
plain (unmarked) assign anywhere in the body, the name in THIS function's
interrogated set (`g_cur_interrogated`), not a parameter, a module NUMERIC
binding (`gnm`; the round-133 demotion already takes any name a function
writes a boxed value into off that map) whose every writer in this body is
numeric under the post-merge maps, the function not observed, and no
nested define/lambda reading the name. Members get round 151's shadow pair
(`double eig_z__loc; int eig_z__isloc`): every write stores into the pair
and traces under the name (`aot_trace_assign`, observing nothing); every
read — `emit_num`'s and `emit_val`'s ident arms — dispatches
`(eig_z__isloc ? eig_z__loc : <this regime's module read>)`, the fallback
computed by re-entering the arm with the mark cleared, so the bit-guarded
static, the int-cast static and the env read (module-observed regime) all
stay exactly what they were. `prev of z` under a module boundness bit
accepts the pair's flag as bound-in-scope. C stack storage makes recursion
exact for free; the pair is suppressed while a loop-scoped for-body over
the same name is emitted (the block-local IS the binding there, EigenScript
#1074), and `g_int` drops the name so no `long` read bypasses the pair. The
round-151 shadow (`g_cur_shadowbit`, writes routed on the module bit) is
pruned of members: their writes are unconditionally call-local.

**CONSTRAINT — still refused by name, each with its own reason in the
message and a `test/refuse/` fixture (the VM runs every one rc 0):**

- **boxed module binding** (`z is "a"`; `interrogated_module_write_boxed`)
  and a **numeric binding written a non-numeric value**
  (`interrogated_module_write_nonnum`): the pair is a C double; a `Value*`
  pair under the owned-read convention (env reads hand out a new ref, the
  pair would have to as well, and release at every exit as the generic-
  parameter rebind does) is not emitted. VM: `a a` / `1 1`.
- **an RHS this pass cannot TYPE**
  (`interrogated_module_write_boxed_rhs`): `z is k` with `k` a plain `for`
  binder in a traced program is an env-boxed read (`g_forunbox` only fires
  untraced), so `is_num_expr` says no even though the value IS a number at
  run time. The VM binds `z` function-local regardless (prev 1, module 1);
  the pair is a C double and an unconditional unbox would die where the VM
  does not, so the name leaves the class and the refusal says exactly that
  ("not PROVABLY numeric here"). The same reason arm covers the genuinely
  non-numeric writer above — one message, both causes named.
- **observed function** (`converged of z` in the body;
  `interrogated_module_write_observed`): Part 2a keys its locals into
  `__eigs_g` by NAME, where the module binding already lives; the VM gives
  the frame binding its own observer slot (`0`, prev 0.25, module 1). The
  per-call env (Part 2b, #123/#124 lineage) lifts it.
- **nested define/lambda reading the name**
  (`interrogated_module_write_nested_ref`): the VM's chain walk from the
  nested frame meets the encloser's call-local (`g of 1` = 1.25); a lifted
  C function reads the module static (2).
- **the bare `what is z` / `when is z`** (no `at`) are a PRE-EXISTING
  program-wide refusal ("temporal interrogative without `at` only supports
  `prev`"), not this shape's; t314 pins the `at` forms.

**Not this class, found while checking the flagship program (residual,
needs its own issue).** `dynamics/physics.eigs`'s `frame-velocity` line
still diverges (VM `-0.38236…`, AOT `0`) — but `x` there is NOT a module
name (every `x is state[0]` sits in a function body), so the round-195
attribution to #218 was wrong. Reduced to one cause
(`scratchpad r2_phys_shape.eigs`): a NON-parameter, non-interrogating
function's plain local is a frame SLOT on the pin, and EigenScript#1063
records a slot's writes only in a chunk that interrogates the name
(`local_traced`); the AOT's `traced_name` applies that rule to parameters
only and tapes every other local's writes. `define stp(st) as: x is st[0] /
x is x + 1 / return [x]` then `fv`: `x is st[0] / st is stp of [st] / x is
st[0] / return x - (prev of x)` prints 1 on the VM (fv's two writes on the
tape) and 0 under the AOT (stp's writes taped too, so `prev` is the value
just re-written). Name-routed locals still tape on both sides (`local w`
over a module name, a plain write to a module name before its binding
exists: VM 6 = AOT's rule) — the mirror is `local_eligible`'s predicate
(slot iff not captured / interrogated / env-bound / outer / module-named),
not "every non-parameter".

Also pre-existing, met by t312's first draft: a module variable named `n`
makes every zero-argument call refuse "passes a 0-element literal list to
its single parameter" (`n is 1 / define bumpn() as: return n / print of
(bumpn of [])`; the base compiler refuses it too) — a name clash inside
the compiler's own arity bookkeeping; t312 uses `m`.

Gates (oracle `/home/user/wt/eigs-pin`, the v0.43.0 pin): `aot/test/run.sh`
→ `381 PASS / 0 FAIL`, `--- bench tier: 12 program(s) build-checked ---`,
`PASS: load_file_shadow (A/B layout from the shadowing cwd + stdlib-from-child
layout, 4 assertions)`, `--- refusal tier: 60 guard(s) exercised ---`,
`--- runtime-refusal tier: 1 residual(s) exercised ---`, `--- all AOT parity
tests passed ---`; `test/run.sh` → `ALL PASSED (63 programs + bootstrap)`.
Planted faults: re-emitting the outward write flips t310 (`print of z`
1 → 0.25) and t311; forcing the read dispatch to the module fallback flips
t311 and t312. `aot/tools/envelope_census.sh` (transpile tier,
CENSUS_TIMEOUT=120, over a symlink root of the 26 ecosystem repos — the
default root under `/home/user` also sweeps the throwaway `wt/` worktrees,
whose population changes mid-run and makes two runs incomparable): base
8fb02d6 and this branch both `transpiles: 81   refused: 35   timeout: 0
(total 116)`, and the per-program verdicts are byte-identical. The class is
absent from the ecosystem corpus, so the count neither rises nor falls —
the envelope gain is measured by the fixtures, not by this instrument.

## F-OURO-43 — module namespacing is the whole answer to splice collisions: the guard is gone, the renamer covers every free write at every depth, recursively — FIXED (#141)

**BUG (in ouroboros; the class the issue was opened on).** `import` (#121)
SPLICES a module's top-level statements into the program, which merges the
two scopes. Three silent divergences followed, each rc 0 with an empty build
log: a program binding a module's private name at depth (`define reset() as:
counter is 999` → VM `0, 1`, AOT `999, 1000`); two modules sharing a
top-level name (`ma.state`/`mb.state` merged — the dict snapshots stayed
right while the FUNCTIONS went wrong); a program reading a module-private
name it never bound (`import mymod; print of counter` → VM `undefined
variable`, AOT `0`, a default-initialised C global). The first answer was a
name-comparison GUARD; four blind-critic rounds found four holes in it, and
round 7's measurement ended it: `is` is outward-mutable, so a bare `ctr is
99` inside ANY module function writes the merged scope — 66 of 77 stdlib
modules do it (892 distinct names, median 10, 56 modules with names as
common as `i`/`n`/`key`). A sound guard refuses nearly every program that
imports anything; a precise one has holes.

**The fix (on the tree since the #121 follow-on rounds; this entry closes the
issue against its bar at the v0.43.0 pin, and fixes what re-checking it
found).** `splice_module` renames a module under its own prefix: every
top-level binding AND every free assignment target at any depth
(`module_bindings` ∪ `collect_free_assigns`) becomes `<M>__<name>`; every
binder — parameter, `for` variable, `try` name, listcomp variable — becomes
`<M>__bnd__<name>` with occurrence-scoped shadowing (`rename_ast`); a
module's own `import` recurses, so a transitively imported module is renamed
under ITS prefix and bound in the importer's namespace as `<outer>__<inner>`
→ `__moddict_<inner>`, the VM's cache identity. The issue body's spec
("rename top-level bindings") is insufficient and the first comment's
correction is what is implemented — arm B is the planted fault below.

**Two boundaries the renamer must NOT cross, both now fixtured.**

- A free **READ** of a name the module never binds is left alone. The VM
  resolves reads and calls dynamically across the module boundary; only
  WRITES stop at its edge (`docs/LANGUAGE_CONTRACT.md`, Modules;
  EigenScript#373/#1056). Renaming it would kill a working program —
  measured on the pin, `_xread.readg` → 42 and `_xread.callg` → 6, VM = AOT
  (t322).
- A module `local` is not renamed either; it is handled by the emitter's
  `g_locshadow`/`g_boxshadow` layer. That covers the one case the issue's
  first comment flagged as maybe needing a refusal by name — `lib/tensor.
  eigs`'s `scale` (a top-level function plus `local scale is …` in two other
  functions), measured there as 1 of 77 modules. It needs no refusal, over a
  top-level function AND over a top-level number: t321 is byte-exact
  (`7/11/110/12/100/1/2`), and `import tensor` still refuses — for the
  unrelated reason that `tensor__linear` is used as a value and takes a
  tensor parameter.

**What re-checking the closure fixed in the renamer itself.** `G_MODPFX` —
the binder prefix — was never restored after the recursive splice of a
module's own `import`, so every binder of THAT module after its `import`
line carried the INNERMOST module's prefix. Measured on t320's three-link
chain: the emitted C carried `eig_ch_leaf__bnd__ctr` 18x and
`eig_ch_leaf__bnd__n` 18x — ch_top's and ch_mid's binders under ch_leaf's
name — where the fixed compiler emits `ch_leaf__bnd__n` 4, `ch_mid__bnd__n`
6, `ch_top__bnd__ctr` 18, `ch_top__bnd__n` 8.

**No output divergence was found for it, and that is stated rather than
dressed up.** A binder is a C BLOCK-local in the emitted code (the file-scope
`static long eig_<M>__bnd__<v>` beside it is never written by the loop), so
two modules sharing one binder name still read their own values: with the
restore removed, t318 and t320 both stay GREEN, and so does a probe crossing
a numeric module-level `for` binder in one module with a string one in
another. It is fixed as a construction bug — the point of a per-module prefix
is that two modules cannot share a name — and its evidence is the symbol
census above, not a red fixture. Restoring once at the end of the branch is
enough; a second restore after each recursion is dead (the inner call's own
restore puts back what it found — emitted C byte-identical with and without
it), so it was removed rather than kept as decoration. `module_shadow_names`,
the last unreferenced limb of the deleted guard, went with it.

**A second bug, found by the census and costing it seven honest rows:
`aot_is_builtin_name`'s `eval of name` probe printed E005 to stderr for
`report`.** Since v0.43.0 (EigenScript#1102) `report` is a reserved observer
FORM, never a value, so `eval of "report"` on the pin PRINTS `Parse error
line 1:1: 'report' is a reserved observer form; use it with 'of variable',
never as a binding [E005]` before throwing into the catch below it. That line
then became the FIRST stderr line of every transpile of a program that
mentions `report` — which is exactly what `aot/tools/envelope_census.sh`
reports as the refusal reason. Measured on
`aot/test/t127_obs_outward_writer.eigs`: base 8fb02d6 prints the E005 line on
a SUCCESSFUL transpile (rc 0), this branch prints nothing.
`report`/`report_value` are answered `0` before the probe, and seven census
rows recovered their real reason (below).

**A third, in the instrument: `envelope_census.sh` silently truncated its own
table.** Under `set -Eeuo pipefail`, a refusal that printed NOTHING to stderr
made the `why=$(grep …)` assignment exit non-zero, which aborted the run
mid-table — no summary line, no remaining rows, and exit 1 against the
script's own documented "Exit: 0 always" contract, so a caller redirecting to
a file is left with a partial table that looks whole. Two runs here stopped
at row 40 of 116 and were read as "stalled" until the exit path was traced;
reproduced deterministically with a fake `EIGS` that exits 3 silently (the
unpatched script stops after the header, rc 1; patched it tables both rows
and summarises, rc 0). The `(no diagnostic)` fallback already written for
this case could never run. Now `|| true`, and the fallback names the exit
code.

**Fixtures** (all byte-exact vs `/home/user/wt/eigs-pin/src/eigenscript`,
v0.43.0): `t96` (body repro 1, importer binds the module's name at depth),
`t97` (repro 2, two modules one name), `t98`/`t99` (the guard's exemption
hole / binder shadowing), and new here: `t308_module_private_read_err`
(repro 3 — both die `undefined variable 'counter'` at line 16, rc 1 both,
`_err` class); `t309_import_transitive` (arm A — `8 / 50 / 1`, and `keys of
_arm_outer` omits `_arm_inner` per the #142 privacy rule);
`t310_module_nested_free_assign` (arm B — `99 / 1`);
`t311_import_chain_three` (`ch_top` → `ch_mid` → `ch_leaf` under
`test/eigs_modules/<n>/<n>.eigs`, the one import fixture that resolves
through the `eigs_modules` step of the VM's chain; `112 / 127 / 127 / 15 / 3
/ 1000` with snapshots `100 / 10 / 1`);
`t312_module_local_shadows_own_fn`; `t313_module_free_read_crosses`.

**Planted fault:** delete the `collect_free_assigns` line from
`splice_module` — i.e. build exactly the issue body's spec, top-level rename
only — and t319 goes AOT `99 / 99` against VM `99 / 1`, rc 0 both, empty
build log: red on output, in the silent-wrong class. t96 (repro 1), t318 and
t320 all stay GREEN under the same plant, which is why arm B is the gate and
repro 1 is not.

**The guard is gone.** `grep -n 'collid\|collision' aot/compile.eigs` leaves
comment mentions, the #165 nested-define guard and the kind-flip guard
("colliding pair") — no name-comparison refusal between a module and the
program — and `aot/test/refuse/` holds no fixture on it (its three became
t96–t98).

**The two road guards (the issue's second comment; EigenScript#1056 landed in
v0.43.0).** Both KEPT, both reclassified: neither is a road-dependence any
more, both are AOT lowering gaps, and each was re-measured on the pin here.

- *Conditional top-level binding* (`module_cond_binds`;
  `refuse/module_conditional_binding.eigs`,
  `refuse/for_body_fresh_binding.eigs`). #1056 made the `for` body uniform,
  so the road-dependent `for` exemption is already gone (it went with the
  #147 bump). What is left is not a road question but a RUN question: on one
  road, one file, `keys of M` is `["a", "t"]` when the `if` ran and `["a"]`
  when it did not, and the AOT builds that dict statically with no boundness
  bit on a C global — including the key invents one the VM lacks, omitting it
  drops one it has. Cost: `test_runner`, 1 of 77 stdlib modules.
- *Top-level `return`* (`module_has_toplevel_return`;
  `refuse/module_toplevel_return.eigs`,
  `refuse/loadfile_toplevel_return.eigs`). #1056 made the rule uniform —
  "ends the current FILE and yields its value" — and the roads now differ
  only in what the caller does with it (measured on the pin, one file `a is 1
  / return 7 / b is 2`: import → `keys of M` `["a"]`, `M.a` 1; `load_file` →
  7; as a main program → rc 0). The splice has no file boundary: a module's
  `return` inside main ends the program. Lowering it needs a per-file
  boundary (a `goto` past the spliced statements, or the module body as its
  own C function) — not built under this issue. Cost: 0 of 77 stdlib modules.

**Measured** (2026-09-07, under load — `uptime` load average 11–13 on a
4-core box shared with ~8 agents; these are COUNTS, not timings, so load
affects only how long they took):

- **Stdlib sweep**, `import M` for each of the 77 `lib/*.eigs` in the pin,
  through the transpiler: **71 transpile, 6 refuse**, and NONE of the six is
  a collision — `concurrent` (nested define captures `concurrent__item`,
  which the encloser rebinds), `eigen` (`lambda`), `observer` (rebinding the
  generic parameter `observer__bnd__val` in an observed program), `supervise`
  (`task_spawn`, ouroboros#188), `tensor` (`tensor__linear` used as a value
  takes a tensor parameter), `test_runner` (the conditional-binding guard
  above). The renamed names in those messages are themselves evidence the
  renamer ran.
- **Envelope census** (`aot/tools/envelope_census.sh` over a symlink farm of
  the 16 consumer repos, `EIGS`/`EIGS_DIR` = the v0.43.0 checkout,
  `CENSUS_TIMEOUT=240`, the patched script on BOTH sides so the only variable
  is `compile.eigs`): base 8fb02d6 **transpiles: 81 refused: 35 timeout: 0
  (total 116)**; this branch **transpiles: 81 refused: 35 timeout: 0 (total
  116)**. No regression, and **seven rows recovered their true refusal
  reason** from the `report` misattribution: `EigenGauntlet/src/cross_lab`
  (really `task_spawn`), `EigenGauntlet/src/observer_lab` (a temporal
  interrogative without `at`), `dynamics/life` (a function assigning a module
  name), `dynamics/orbit`, `dynamics/predicate_calibration`,
  `dynamics/predicate_fit` (`local` shadows of module bindings) and
  `iLambdaAi/scripts/generate_transformer` (`lambda`).
- **phugoid: 8 of 9 both before and after**, the recovery the issue's first
  comment demanded (it had fallen to 3/9 under the sound guard). The one
  refusal, `swarm.eigs`, is the unrelated nested-define outward-write guard.
- The issue comment's "census 85 → 79" is not directly comparable to these
  totals: discovery and the corpus have both changed since (116 rows now).
  What is comparable is the ratio's direction and phugoid's 9 rows, and both
  are recovered.

**Residual (probed, not fixtured — no home in the harness).** An `import`
cycle: the VM raises `import: circular dependency — 'cyc_a' is already being
loaded`; the AOT refuses at BUILD time, loudly and with a different message
("top-level use of module name '__moddict_cyc_a' precedes its first
module-level binding"). Both die, neither is silent, so nothing is at risk —
but it fits no tier: `refuse/` requires the VM to RUN the program, and the
parity tiers require a binary. The `G_IMPORTED[mname] is 1` mark placed
BEFORE the recursion is what terminates the walk; moving it after turns the
refusal into `call stack overflow` from the parser (still loud, rc 1, no
binary).

Gates at a6c50fb (`EIGS=/home/user/wt/eigs-pin/src/eigenscript
EIGS_DIR=/home/user/wt/eigs-pin`): `bash aot/test/run.sh` → 376 PASS lines,
`--- bench tier: 12 program(s) build-checked ---`, `--- refusal tier: 56
guard(s) exercised ---`, `--- runtime-refusal tier: 1 residual(s) exercised
---`, `--- all AOT parity tests passed ---`, rc 0; `bash test/run.sh` → `ALL
PASSED (63 programs + bootstrap)` incl. `PASS: bootstrap fixed point (and the
self-compiled program runs)`, rc 0.
