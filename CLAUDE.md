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
| `test/run.sh` | Self-host suite: `test/programs/*.eigs` parity + `reject_one` cases + the **byte-exact bootstrap fixed point** |
| `aot/test/run.sh` | AOT parity suite: every `tN_*.eigs` diffed byte-for-byte vs the VM (`*_tol*` = FP tolerance) |
| `aot/fuzzdiff.py` | Differential fuzzer across the edge classes (`--count`, `--seed`) |
| `FINDINGS.md` | The F-OURO-NN ledger — GAP/BY-DESIGN/CONSTRAINT/TRACKING conventions; read before re-investigating anything |
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
  match `strtod` (scientific, leading-dot, hex with `p`-exponent and
  lookahead guards); f-string desugaring parenthesized as one primary;
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
  at build time (F-OURO-23) rather than guessing. Full semantics parity
  is proven at the self-host tier; the AOT proves the subset it claims.
- **Opcode numbers are an ABI** — new opcodes append at the enum end
  (hand-built bytecode in upstream tests hardcodes them).
- **Deep procedure lives in the `aot-differential` skill** (and the
  eigenscript-aot-compiler-engineer skill); this file is the orientation.

## One-line test

Does the output equal `eigenscript PROG.eigs` byte-for-byte, do BOTH
harnesses pass against the PINNED oracle, and — if the frontend changed —
does it still match the canonical C parser?
