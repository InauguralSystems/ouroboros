The len probe uses scalar-production generated C from compiler implementation
`7d6b6fa`, merged as `6616fb7` in ouroboros #227. Its frozen C SHA256 is
`a2d149ba8997c665db5144f1a1c5e1874658e0195df2cbc0aafc257c53106df1`.
EigenScript v0.43.0 is `a6c50fba6a6250ea347a34500d6c9fa503a5c931`;
the certificate runner is EigenMiniSat #112, with regime-C solver code
unchanged. Recorded source snapshots and exact binaries are identified in
the comparison manifest. Native policy differs from EMS policy.

The [generator](len/preparation/prepare.py), guarded helper, control program,
validator and complete build recipes are retained. Generated C, the copied
production header, executables and object archives are excluded from this
bank. Rebuild them from the pinned sources, retaining the declared hashes.
The candidate source is a reversible transformation of the frozen scalar C.
The runtime archive SHA256 is
`5db0ea14d1ce3476b03643622fea11359172075b21cdc6343d80a9e591bd6eca`.

The [stage runner](recipes/run_len_probe.py) selects preparation preflight,
controls, a diagnostic workload, fresh uninstrumented builds, and comparison.
Its external preparation fingerprint is
`a56415277e58ec8febd416a056a50b78d81cc3ea63467d6b139e98f664bbfca6`.
It validates every prepared source, control, validator and recipe before use
and around stages; completed stage/build records bind the same fingerprint.
The saved preflight controls use the actual runner CLI.

All recipes retain their original absolute paths. Recreate those inputs or
adapt the paths in a new preparation directory with a new explicit inventory;
do not quietly substitute a current compiler checkout for the recorded source.
The original comparison used the clean canonical compiler at `6616fb7`.
It calls `compare_native.main` in the same process so timeout/cancellation
cleanup remains with the solver/checker runner. Solver/checker limits are
7,200 seconds, overall budget 21,600 seconds, and proof-off pilots must finish
within 600 seconds before n=5 repetition. One heavy job runs at a time.

The independent GC experiment uses the same frozen scalar C and header. Its
[generator](gc/preparation/prepare.py) applies 17 reversible edit groups
(18 replacements) to the pinned `src/eigenscript.c`; the replacement ledger
and original source hash are retained. Runtime C snapshots, generated EMS C,
objects and executables are excluded from this bank. Reconstruct them from
the pinned sources and recipes. The preparation README is the historical
design note from before execution; saved process records determine what ran.

[The GC build runner](gc/preparation/run_prepared.py) requires the external
preparation fingerprint
`e42272d67a81433bbb65372d57fcce7d9c276c5e227f173477417780d2fe1b87`.
It checks all prepared files, 20 runtime headers, the runtime archive, pinned
runtime source and process runner before imports and around every subprocess.
It compiles a temporary runtime TU and links it before the existing archive;
retained link maps confirm the original archive member was not extracted.
Baseline timing defines are empty; candidate defines only
`GC_TRAVERSAL_REUSE`. Diagnostic and fault macros are absent from timing builds.

[The workload runner](recipes/run_gc_workload.py) requires completed controls
and a validated diagnostic result before timing. It caches each initial build
manifest and its expected object/map/binary hashes throughout a stage. A
lightweight control using the actual helper rejects a changed binary plus
rewritten manifest that the previous behavior accepted. It is a helper-level
control with mock artifact bytes, not a solver or compiler run.

GC diagnostic output matches the frozen VM reference and the real validator
compares complete collection sequences. The certificate-aware timing runner
then validates the usual VM anchors, models and proofs. The compiler snapshot
was clean `aadbb84`; its implementation is unchanged from #227. The canonical
EMS snapshot records an existing untracked `benchmarks/__pycache__` file and
its hash; the runtime/compiler snapshots are clean. Current EigenScript main
is ahead of v0.43.0, so these results must not be presented as current-main
measurements or as a completed compiler pin update.

The [larger-rung wrapper](recipes/run_larger_ladder.py) imposes a 3 GiB soft
address-space limit on itself and its children, retaining the existing
7,200-second solver/checker cap and 600-second pilot requirement. It invokes
the [production driver](recipes/run_next_probes.py) in the same process,
restores limits before final identity checks and preserves failed attempts.
The 5×6 manifest remains incomplete. Its AOT stderr reports a failed 72-byte
allocation and the process record reports SIGABRT, with no timeout or external
interrupt. The observed delay in crash-dump handling is included in its process
duration; neither that duration nor the one native certificate solve is an n=5
performance point. No source or solver policy was changed for the attempt.

The [6×6 outer wrapper](recipes/run_6x6_no_core.py) also runs in the same
process. It sets a one-byte soft core limit, which Linux recognizes as the
sentinel that skips a piped crash helper; an ordinary zero core limit is ignored
for that path. The linked kernel source in the wrapper explains the behavior.
The [actual abort control](ladder/core-limit-control/result.json) exited with
SIGABRT in 0.069 seconds. Ten separate in-memory wrapper controls checked
restoration, failure propagation, identity drift and evidence protection; they
did not change real process limits or run the ladder. The 6×6 failure occurred
with no timeout or external interruption, and all limit/identity restoration
records are retained. These failed process durations are not performance ratios.
