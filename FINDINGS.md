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
OP_SET_FN_NAME_LOCAL; (3) not in_outer and not in_module →
local-eligible, fresh anonymous slot (`local` included); (4) ineligible
+ `local` → OP_SET_FN_NAME_LOCAL (the explicit shadow); (5) ineligible
plain assign → OP_SET_NAME (outward mutation of the enclosing or module
binding). test/programs/enclosing_scope.eigs pins read-modify-write,
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
tracked in #106. The AOT additionally refuses `when` (no occurrence-ring
seam) and module-shadowing `local` loudly. Growing any of these is new
work, not a bug; this entry is the ledger naming them.
