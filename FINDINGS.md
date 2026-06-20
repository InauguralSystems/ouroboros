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

*(Further findings — local-slot allocation for functions, closure cycles, and
observer-opcode parity — to be added as the roadmap lands.)*
