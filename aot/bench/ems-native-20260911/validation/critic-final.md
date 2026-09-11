# Slot-index optimizer review

Verdict: **no concrete residual optimizer issue found** in the frozen patch. This is a focused review supported by executed artifacts; the root's full seven-tier gate and instruction measurements remain separate completion requirements.

Reviewed files: `/home/jon/src/wt/ouro-ems-20260910/aot/compile.eigs` and `aot/aot_rt.h`, against baseline `368603bf96a74382b16819d7bcb406d246343d89`.

Verified SHA-256 identities:

- Compiler: `1a2e7bba929879c4a3616cc7c7aeed4266b4ac09895d0f657bcff5f5125256e1`.
- Runtime header: `7a2a091ef0e2117c801b32dfc7853e41130e10f82bf96658afca4b1a9b64e38f`.
- Oracle checkout: clean, exact tag `v0.43.0`, commit `a6c50fba6a6250ea347a34500d6c9fa503a5c931`.

The emitter only borrows actual lowered frame slots, excludes source calls and competing storage classes, and stages target then index before overwriting the destination. Its preceding numeric-RHS arm still wins; the initial concern about bypassing that conversion was disproved by reading the full enclosing dispatch. The helper copies numeric results before destination release, retains pointer results before aliasing releases, preserves arena promotion, and retains the existing exceptional-index fallback. Heap list numbers preserve raw slot semantics; buffer and promoted arena numbers preserve their existing guards.

I independently compared the saved VM/native bytes and inspected emitted helper calls for all six fixtures. All six stdout pairs match, with empty VM/native stderr. t337 reaches `_s`, `_i`, and `_v` (6/7/2 sites); t340 reaches 1/4/1; t341 reaches 1/4/0. Callback, traced, and observed controls t338/t339/t342 each contain zero optimized calls. This supports both active paths and exclusions, rather than parity alone.

The production C has 19 optimized calls: 9 in `eig_run_watched_queue_cdcl`, 3 in `eig_run_watched_queue`, 2 in `eig_analyze_mark_store_clause`, 4 in `eig_lit_redundant`, and 1 in `eig_run_proof_benchmarks`. The targeted propagation loop is reached.

`asan.log` and the retained sanitizer reports show direct old/new control at 253/253 leaked allocations (18,216/18,216 bytes), and generated t341 old/new at 253/252 allocations (18,216/18,144 bytes), with identical completion output. These are **no-new-leak** results, not leak-free results. `ownership-gate.log` independently records two witnessed arms at 253/253 with no slack.

All six planted faults were rejected. I checked their mutation construction and retained diagnostics: missing element retain produces an ASan heap-use-after-free in `check_aliases`; missing destination release raises the leak count from 253 to 2,353; extra heap-number guard and missing buffer guard fail their specific number-boundary assertions; accepting fractional list/buffer indices changes t340's VM-matched output. These are real executed failures, not merely rejected compilation or nonempty-error checks.

Supporting consumer results are present: the production wall manifest records 45 validated samples across three cases and three arms; the proof-check log reports all refutations verified, including rejection of a deliberately truncated proof; differential fuzz reports 200 cases with 84 verified UNSAT proofs and zero failures; policy fuzz reports 240 solves with 138 verified proofs and zero failures, with reduction/compaction/rebuild paths exercised. I have not treated the wall results as proof of an instruction reduction or a universal speedup.

Review limitations: no new builds, VM runs, tests, or benchmarks were launched by this critic. This pass inspected source, generated C, saved outputs, fault reports, and provenance. No core files were edited. The seven-tier driver itself is outside this optimizer review and has a separate critic.
