The measured compiler baseline is ouroboros
`328e7692be660b0f56ff73f752207fb25474101c`. The candidate is that revision plus
[candidate-core.patch](candidate-core.patch), also present as the compiler and
runtime-header change in the commit containing this bank. The unchanged
frontend is taken from the same baseline commit.

Both use EigenScript v0.43.0,
`a6c50fba6a6250ea347a34500d6c9fa503a5c931`, in the clean runtime worktree
`/home/jon/src/wt/es-v043`. The fresh release archive's SHA256 is
`5db0ea14d1ce3476b03643622fea11359172075b21cdc6343d80a9e591bd6eca`.
No prebuilt executable was copied into another tree.

Both EMS binaries use solver source from
`6d06b04336b96da83a26ac22d4a5b908aad52983`. The timing harness was the exact
worktree contents later committed as EigenMiniSat `58045ac` and merged as
`c5784a3` in PR #112; those harness changes did not alter the solver.
The original manifest records the pre-commit worktree and all source hashes.
The baseline was built in the new compiler worktree before it was edited;
the manifest points to the identical clean canonical compiler baseline so it
does not attribute that binary to the later candidate edits.

The generated C identities are:

| Arm | SHA256 |
|---|---|
| Baseline EMS | `ddec94a97948a7f178f924c9b2578b07c0804eed69e7a00b5629c26a0c5c53b2` |
| Candidate EMS | `a2d149ba8997c665db5144f1a1c5e1874658e0195df2cbc0aafc257c53106df1` |

The exact production compiler arguments are in
[scalar-production-build.json](recipes/scalar-production-build.json). Builds
use `-O3 -ffp-contract=off -falign-functions=64 -falign-jumps=32
-falign-loops=32 -march=native`, optional HTTP/model/DB extensions disabled,
and explicit script/runtime directories. Candidate C and header hashes are
captured in [the focused manifest](validation/focused/manifest.json). Fresh
DMG source/build identities are in [consumer-builds.json](consumers/consumer-builds.json).

To reproduce on another checkout, substitute its absolute paths in the retained
[stage-two recipe](recipes/run_stage2.py) and
[consumer recipe](recipes/run_consumers.py). First build baseline and candidate
with their respective compiler source and the same freshly built pinned
archive. Then run the certificate timing comparison using a fresh output
directory, explicit generated C/build provenance, the pinned runtime, native
MiniSat and drat-trim. The stage-two `scalar-wall` command uses the two VM
anchors, pigeonhole and SAT controls, plus 4×5. It automatically verifies
certificates/pilots before timing, rotates arm order, and validates all 75
samples. Commands in this bank retain original machine paths; they are records,
not relocatable scripts without path substitution.

For new long runs, invoke `compare_native.py` directly or call its `main` in
the owning Python process. The retained stage-two recipe wrapped it in another
`run_process`; that outer session cannot clean up the runner's separately
created solver session if the wrapper is cancelled. All measurements banked
here completed normally, so this cancellation limitation does not affect their
results. The runner itself owns the solver/checker deadlines and cleanup.

Native MiniSat was `/usr/bin/minisat`, package `1:2.2.1-8build1`. The checker
was built from drat-trim commit `2e3b2dc0ecf938addbd779d42877b6ed69d9a985`;
the exact executable hashes are in [the timing manifest](timing/manifest.json).
Each proof and checker process record is retained in [certificates](timing/certificates/).
Native uses default policy; EMS uses regime C with `--cdcl` and no overrides.

Hardware events and wall samples are separate runs. The consumer recipe's
`build`, `counters` and `wall` stages each run serially; hardware counters may
require permission from the host. Every measured DMG output must match the
tracked canary and report exactly 50,000,008 emulated cycles. Do not overlap
these jobs with builds or other suites on the two-core development box.

The original probe/diagnostic recipes are retained under [recipes](recipes/).
They deliberately generate isolated scratch sources and do not define
production semantics. Raw prototype outputs, generated C, ASan archives,
diagnostic object/link-map products and `perf.data` remain local build artifacts;
their controls, summaries, identities and regeneration recipes are recorded.
The banked `oracle/discovered` and `oracle/sorted` files are NUL-delimited path
manifests from the original oracle, not executables.
