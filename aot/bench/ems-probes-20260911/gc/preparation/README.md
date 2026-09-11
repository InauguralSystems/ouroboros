# Collector traversal reuse — prepared source only

No compiler, test, validator or workload has been run for this probe. Root owns
the only heavy-job slot. Source preparation and exact reversal assertions ran.
There are no changes to pinned runtime, compiler, or EMS working trees.

The frozen runtime is v0.43.0 a6c50fba6a6250ea347a34500d6c9fa503a5c931.
`source-pin.json` records runtime/scalar-header/scalar-C identities.
`eigenscript-original.c` is untouched source; `eigenscript-probe.c` has 17 exact
replacement groups (18 occurrences), reversible through `replacement-ledger.json`.
`aot_rt.h` and `scalar.c` freeze scalar production, without len/vector/range/append
probe changes. `manifest.json` and `PREP-HASHES.json` cover generated files,
preparation code, controls, recipes and the runtime archive identity.

Regenerate into a NEW directory (source generation only):

    python3 prepare.py --source eigenscript-original.c --out /tmp/gc-regenerated

This regenerates runtime C, metrics header/implementation and replacement ledger;
the original preparation directory retains the separately authored controls,
runner, scalar/header snapshots and build provenance. Existing differing files
are refused. The generator asserts reversing its entire edit sequence restores
the original source exactly.

## One hypothesis, two compile-time arms

Without GC_TRAVERSAL_REUSE the original three traversals remain. With it:

1. Universe discovery calls the SAME GC_FOR_EACH_CHILD over the SAME edge table.
   Each accepted edge adds its target and increments that target's internal
   reference count immediately. Duplicate edges and self-edges each count.
   The original separate counting traversal is absent.
2. A collection-local byte per node records whether discovery found any node
   child. Marking skips child traversal only for nodes with that byte clear.

Indices survive gcu_add reallocation; the code never retains a pointer into a
resizable node array across that call. The byte array follows existing capacity
growth, is initialized per new node, and is freed on both accounting-abort and
normal completion paths. It adds O(universe capacity) transient bytes. Its fact
is valid only during this stopped collection, before edge clearing begins.

Triggers, threshold formulas, seeds, pins, multithreaded/recursive exclusions,
edge table, root test, accounting-abort behavior, clear operations, and pin-buffer
draining are unchanged. This does not suppress GC or assert that numeric lists
remain permanently leaf-only. The extra lookup per discovered child and new
array allocation may offset savings; no speedup is asserted.

## Diagnostics and coverage

GC_PROBE_DIAGNOSTICS counts actual gc_collect_impl entries, early returns,
started/completed/aborted collections, unique universe nodes, reclaimed nodes,
surviving nodes and input seeds. A record for every completed collection permits
exact population-sequence comparison; totals print once at process exit.
Skipped gc_collect_cycles calls that return BEFORE gc_collect_impl are not part
of its entry census. No output/truncation/timeout is not a successful census.

For discovery, original counting, marking and clearing, inspected slots are
counted per value/env/chunk kind. Accepted edges are counted after IS_NODE in
the first three phases, including duplicates. Clearing counts all owned slots
and has no accepted-edge predicate, so its edge counters remain zero. These are
phase slot visits, not unique graph edges or unique allocated objects. Numeric
leaves rejected by discovery still contribute inspected slots. The counters are
entirely preprocessor-elided from timing builds.

`control.c` builds a live parent with duplicate references to a 64-number list,
and a dead two-node cycle with duplicate owned edges. First collection expects
four nodes, two reclaimed, two surviving, 69 discovery slot visits and five
accepted edges. Original and candidate should preserve all live contents.
`validate.py` compares actual collection records and counter partitions, requires
removal of the old counting phase, unchanged discovery/mark-edge/clear populations,
and a nonzero no-child skip with reduced marking-slot visits.

Two planned rejection controls:

- GC_PROBE_FAULT_DROP_DUP_COUNT deliberately treats repeated incoming edges as
  one. The C control must fail its reclaimed-cycle assertion, and the diagnostic
  comparison must reject changed population/reclamation. This fault undercounts
  internal ownership; it is expected to retain garbage, not intentionally free
  live objects.
- GC_PROBE_FAULT_ZERO_SKIP_COUNTER only suppresses the skip metric. Program
  output should remain green; the real validator CLI must reject missing reach.

None of those outcomes has been observed yet. Controls do not cover env/chunk
cycles, collector array growth, multithreading, arena boundaries, allocation
failure, or full runtime ownership. Existing runtime release/ASan suites and
closure-cycle controls remain mandatory before any production consideration.

## Later execution, explicitly scheduled by root

`run_prepared.py` imports the original compare_native bounded subprocess/group
cleanup. It performs serial compile/link/run operations, saves process records,
exact commands and hashes, and refuses reused output directories. No binary is
copied and no runtime archive is rebuilt. It compiles the temporary eigenscript
TU and links that object explicitly BEFORE the immutable baseline archive;
link maps must exclude the archive's original eigenscript.o member. Scalar
production GCC flags and runtime include paths are retained.

Every runner invocation requires `--prep-sha256 PIN`, where PIN is the inventory
SHA256 retained externally in the preparation handoff. The runner checks that
fingerprint before parsing the inventory, importing compare_native, or consuming
source/build arguments. It checks all prepared files (including itself and its
candidate flag definitions), 20 runtime headers, pinned runtime source, archive,
and compare_native source before/after each build/process and before the final
verdict. `run.json` records the caller fingerprint, complete expected inventory,
and individual checkpoints. Rewriting the inventory to match modified inputs
cannot satisfy the original external fingerprint. This is boundary verification,
not filesystem locking against a hostile concurrent modify-and-restore race.

The lightweight `preflight` stage imports no helper and executes no subprocess:

    python3 run_prepared.py preflight --prep-sha256 PIN --out /tmp/gc-preflight

Deferred actual CLI provenance controls (NOT run during preparation) copy only
small scratch sources, accept the pristine snapshot, and reject a changed
candidate flag, changed candidate C, and an inventory rewritten to match changed
C. They require the original external pin throughout:

    python3 preflight_controls.py --prep-sha256 PIN --out /tmp/gc-preflight-controls

    python3 run_prepared.py controls --prep-sha256 PIN --out /tmp/gc-control-run
    python3 run_prepared.py diagnostic-build --prep-sha256 PIN --out /tmp/gc-diagnostic-build

Only after successful controls, root can execute both diagnostic binaries on
the same bounded certified EMS case, retain stdout/stderr and process-tree RSS,
require EMS output/counter/proof parity independently, and invoke:

    python3 validate.py BASELINE_STDERR CANDIDATE_STDERR

Do not time diagnostic binaries. If population sequences diverge, investigate
before timing; do not normalize the discrepancy away. Then:

    python3 run_prepared.py timing-build --prep-sha256 PIN --out /tmp/gc-timing-build

Use the existing certificate-aware comparison runner for interleaved n5,
per-process wall/instructions and RSS under an explicit hard memory/time cap.
Start with the established small rung; the observed live 5x5 RSS snapshot was
unprofiled and is neither a peak claim nor authorization for an unbounded run.
No change to regime-C search, heuristic policy, proof handling, or output
normalization is part of this probe.
