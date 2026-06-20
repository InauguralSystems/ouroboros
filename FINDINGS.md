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
`counter` while a function's own temporaries stay local. (The `local` keyword is
not yet covered — the vendored front-end doesn't tokenize it; for-loop variables
bind via `SET_NAME_LOCAL`/`GET_NAME`, correct unless a loop var name collides
with a local slot in the same function.)

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

*(Further findings — closures over enclosing-function locals and the `local`
keyword — to be added as the roadmap lands.)*
