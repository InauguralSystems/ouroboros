# CLAUDE.md — ouroboros working guide

Two compilers live here, both written in EigenScript, both judged by the
same rule: **the bytecode VM is the byte-exact oracle.** A divergence is a
bug in THIS repo, never the VM — reproduce it minimally, fix at the root,
add a differential test. The single worst outcome is a silent wrong
number; a loud throw always beats a coercing guess.

## Layout

| Path | Role |
|---|---|
| `src/frontend.eigs` | The **second parser** (lexer+parser shared by both compilers) — must match `EigenScript/src/parser.c` exactly |
| `src/codegen.eigs` | Self-hosting compiler: AST → the C VM's bytecode (`vm_run_bytecode` runs it) |
| `aot/compile.eigs` | AOT compiler: AST → C, linked against the runtime (`aot/aot_rt.h` helpers) |
| `aot/build.sh` | `eigenscript compile.eigs PROG > gen.c && gcc gen.c libeigsrt.a` (`EIGS_DIR` defaults to `../../EigenScript`) |
| `test/run.sh` | Self-host suite: `test/programs/*.eigs` parity (`*_err.eigs` = both sides must die) + `reject_one` (both parsers reject) + `must_reject` (C rejects at runtime, ouroboros must too) + the **byte-exact bootstrap fixed point, which also EXECUTES the self-compiled program** and diffs its stdout vs the C evaluator (#102) |
| `aot/test/run.sh` | AOT parity suite: every `tN_*.eigs` diffed byte-for-byte vs the VM (`*_tol` = FP tolerance; `*_err` = both die, normalized message, **equal death codes**). Exit codes are part of every class's contract |
| `aot/fuzzdiff.py` | Differential fuzzer across the edge classes (`--count`, `--seed`); VM-rejected programs are error-class cases (AOT must refuse or die matching), never skipped |
| `FINDINGS.md` | The F-OURO-NN ledger — BUG/GAP/CONSTRAINT/BY-DESIGN conventions; read before re-investigating anything |
| `.devcontainer/Dockerfile` | `ARG EIGS_REF=vX.Y.Z` — **the pin**; CI builds this runtime and runs both suites against it |

## Run / test

```bash
bash test/run.sh                    # self-host parity + rejects + bootstrap
bash aot/test/run.sh                # AOT byte-exact parity
cd aot && bash build.sh P.eigs out  # one program; diff <(eigenscript P.eigs) <(./out)
python3 aot/fuzzdiff.py             # differential fuzzing
```

## Hard-won rules

- **Run differential tools against the PINNED VM, not local main.** Build
  the pin from a worktree (`git -C ../EigenScript worktree add DIR vX.Y.Z
  && make -C DIR`) and pass `EIGS=DIR/src/eigenscript` (+ `EIGS_DIR=DIR`
  for the AOT lib). Local main ahead of the pin flags unreleased language
  features as false "drift". A frontend change that needs an unreleased
  feature lands **with** the `EIGS_REF` bump, never ahead of it (the
  deferred-mirror pattern: #326/#328 at v0.21.2–v0.23.0, #355/#351 at
  v0.24.0).
- **Matched-bug canaries police the pin.** When the pinned oracle has a
  confirmed bug the frontend must MATCH (the oracle wins even when wrong),
  encode it as a `test/programs/*_gap.eigs` canary naming the upstream
  issue. It fails the moment a future pin closes the gap — that failure IS
  the reminder to drop the exemption in the same bump (worked exactly as
  designed for #351 at the v0.24.0 bump). `reject_one` asserts the C
  oracle ALSO rejects, so reject cases can't rot into over-rejections.
- **`frontend.eigs` drifts silently — mirror `parser.c` precisely.**
  Verified pain points: `of` binds unary-or-tighter; numeric lexing must
  match the pinned oracle (scientific + leading-dot with lookahead
  guards; since v0.25.0 hex is INTEGER-only, lexed not strtod — the 0x
  prefix is decisive and hex-float forms are loud parse errors);
  f-string desugaring parenthesized as one primary;
  postfix is per-primary; the full precedence chain
  `or→and→cmp→bitor→bitxor→bitand→shift→add→mul→unary→call→primary`.
  Since v0.24.0: a parenthesized literal list carries a 3rd marker slot
  (`["list", elems, 1]`) and never spreads (#355).
- **AOT return-type metadata considers EVERY return** (`collect_return_
  nodes`); mixed-type returns are generic `Value*`. When declared type
  and emission can't share one inference, coerce at the boundary
  (`g_ret_type`) — value-preserving keeps byte-exactness.
- **The AOT envelope is deliberately partial** — defaulted params,
  under-arity calls, and whole-list args to user fns all **throw loudly**
  at build time (F-OURO-23) rather than guessing. Since the 2026-08-16
  train, also loud at build time: the `when` qualifier, module-shadowing
  `local` on BUFFER globals (numeric shadows compile to C block-scope
  locals since #109 — F-OURO-33; BOXED string/list/dict shadows are
  per-call-env bindings with runtime chain dispatch since #86's
  F-OURO-35 — nested-fn reads, binder re-binding and observed/temporal
  functions stay refused), and plain fn-body writes
  to builtin names (F-OURO-31/32);
  tensor-add broadcast/mixed-shape refuses at *runtime*. Full semantics
  parity is proven at the self-host tier; the AOT proves the subset it
  claims.
- **Opcode numbers are an ABI** — new opcodes append at the enum end
  (hand-built bytecode in upstream tests hardcodes them). **So is every
  operand's WIDTH**, and that half has no upstream number-guard. v0.33.0
  widened `OP_LINE` 16→32 bits (#630) with no opcode renumbering, so nothing
  upstream failed; our `cg_emit_u16` then left the VM reading the next
  instruction's first two bytes as the line number's high half and resuming
  misaligned. Every chunk we produced ran as garbage — empty output, exit 0,
  all 44 parity programs — while `bootstrap fixed point` still PASSED, because
  back then self-host-compiling ourselves compared bytes and never executed
  the result. That exact hole is closed (#102: rebinding sentinels + the
  self-compiled program is executed and diffed), but the lesson stands:
  **a green check that never executes its subject is not evidence.** At every pin bump,
  diff `src/vm.h`'s enum comments for operand-width changes, and treat an
  emitter change as landing WITH the `EIGS_REF` bump (the deferred-mirror
  pattern above), never before it.
- **Deep procedure lives in the `aot-differential` skill** (and the
  eigenscript-aot-compiler-engineer skill); this file is the orientation.

## One-line test

Does the output equal `eigenscript PROG.eigs` byte-for-byte, do BOTH
harnesses pass against the PINNED oracle, and — if the frontend changed —
does it still match the canonical C parser?
