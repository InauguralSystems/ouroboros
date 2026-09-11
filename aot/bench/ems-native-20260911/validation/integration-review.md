# Independent integration review — 2026-09-11

Verdict: no remaining concrete finding within this review's scope. One actionable integration gap was identified and closed before this report: the repository originally tested output parity and the helper functions directly, while the generated-call assertions lived only in a temporary script. Disabling the emitter could therefore remove the optimization without a durable test failure.

The fix is now enrolled in `aot/test/run.sh`: `aot/test/slot_index_emission.py` checks six actual fixture transpiles. I independently exercised both directions after its builder handed over the sole heavy-job slot. With the compiler from HEAD `368603bf96a74382b16819d7bcb406d246343d89`, transpilation succeeded and the new gate rejected t337 specifically for expected all helper paths, got `{s: 0, i: 0, v: 0}` (exit 1, completed 0/6, no completion marker). With the candidate compiler, all six fixtures passed (exit 0): t337 s=6/i=7/v=2; t340 s=1/i=4/v=1; t341 s=1/i=4/v=0; t338/t339/t342 all zero. No C build or solver benchmark was run during this control.

The initial scratch lacked `eigs.json`, so the pinned runtime refused the frontend load. My control rejected that as the wrong failure; after copying the project marker, the decisive no-emitter rejection above was obtained. The final logs retain the correct controls.

Core review: inspected actual changes to `aot/compile.eigs` and `aot/aot_rt.h`, their surrounding storage-class dispatch and slot conversion routines in pinned EigenScript, callback/traced/observed exclusions, fallback behavior, and alias/arena/number-boundary tests. No concrete new ownership or semantic fault found. In particular, pointer elements are retained before destination release; numeric contents are copied before release; owned boxed indices retain their fallback representation; and runtime tags and integer/bounds checks decide the fast path. This was code review, not an independent sanitizer execution.

Evidence review: independently recomputed all nine wall-time median/min/max cells from 45 measured records, checked all 20 hardware-counter records against raw perf CSVs and their n=5 aggregates, and matched EMS sample outputs after removing exactly one trailing numeric ms= field. Reviewed compiler/header hashes agree with the candidate provenance. The generated EMS C differs at the intended slot-index assignment sites. The bank accurately labels native solver policy/conflict differences, the short-case overlapping ranges, the DMG hardware-cycle regression, and existing shutdown leaks.

The 4x4 odd-Tseitin wall medians are 10.230163098 s baseline, 9.197103458 s candidate, and 0.264683674 s native. User-mode instruction medians are 17,990,521,397 baseline and 16,377,520,679 candidate. DMG host instructions per emulated cycle are 249.89247097720465 and 249.92377929219532, while its hardware-cycle median increases by roughly 3.05%; this review does not call DMG runtime unchanged.

Reviewed supplied validation logs report six focused pairs/emission checks, direct/generated ASan A/B without added leaks, six rejected actual helper faults, proof checks, 200 differential-fuzz instances, and 240 policy-fuzz solves. I did not independently repeat those heavy jobs. The full seven-tier gate and CI remain the root agent's responsibility; the bank correctly still says PENDING at report time. A previously ignored banked .out-file issue is now addressed by the bank's own ignore exception, and the files are visible in git status.

Independent evidence:
- `/tmp/ems-gap-20260911/integration-emission-old-HEAD-emitter.log`
- `/tmp/ems-gap-20260911/integration-emission-candidate-emitter.log`
- `/tmp/ems-gap-20260911/integration-emission-controls.json`
- `/tmp/ems-gap-20260911/integration-data-recheck.log`

Distillation: the concrete lesson is already enforced by the new emission gate and the independently demonstrated old-compiler fault. No duplicate skill prose or unrelated repository edit was added.
