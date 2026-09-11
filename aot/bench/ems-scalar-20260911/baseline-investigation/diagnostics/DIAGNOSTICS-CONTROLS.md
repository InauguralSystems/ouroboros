# Temporary EMS diagnostics

Generator: `python3 /tmp/ems-close-20260911/make_diagnostics.py --out /tmp/ems-close-20260911/diagnostics`

No diagnostic build or generator execution has been performed by the author.
Run only after the timing slot is released. The generator asserts exact source
matches and records original hashes. Inputs are read-only; outputs are copies.

Build a separate runtime archive using the emitted eigenscript.c in a scratch
worktree with the pinned source's sibling headers and other translation units.
Compile that unit and generated EMS C with `-DEMS_DIAGNOSTICS`; generated EMS C
must include the emitted aot_rt.h, not the production header. Preserve the exact
normal production flags and source inventory otherwise. Never overwrite or link
the production archive. No diagnostic timings are performance evidence.

Output is one stderr line prefixed `EMS_DIAGNOSTICS_JSON ` at normal exit.
It includes teardown. Compare program stdout against the same normal reference
used by the timing arms (only established trailing numeric ms normalization).
Keep all other stderr intact. A signal/abort may prevent the atexit report.

## Cheap C controls to build and run later

A standalone control C unit should include the emitted aot_rt.h and
ems_diagnostics_metrics.h, boot with aot_boot(), and use emsd_snapshot before
and after each operation. Snapshot copies counters without allocations.
Do not infer deltas from whole-process boot/teardown totals.

1. Call make_num once and keep its result live until after the second snapshot.
   Request delta must be 1; fresh heap + arena + reuse deltas must sum to 1.
   With arena inactive, arena delta is 0. Then release that number and call
   make_num again: it must reuse the newly returned freelist entry. Permanent
   constructor delta from one make_num_permanent is exactly 1 and ordinary
   make_num request delta remains 0. The permanent category always allocates
   on the heap and must be added separately to heap allocation totals.
2. With arena inactive, make_list(0), then append nine make_null() values using
   list_append_owned. Expect list_creates +1, initial slots +8, append requests
   +9, successes +9, heap reallocations +1, added slots +8. No make_num requests
   occur. This explicitly detects accidental append-wrapper double counting.
3. make_list_heap(13) must increment only forced-heap creation +1 and initial
   slots +13. list_append(NULL, make_null()) increments requests +1 and
   successes +0, separating requests from successful mutations.
4. Use make_builtin(builtin_len), make_list(0), and aot_call_dispatch to issue
   one len call. Histogram total and builtin dispatch count each increase by
   one. Alias that same builtin value under two environment names and repeat:
   both calls must share one actual pointer identity entry. Labels record only
   the first registered name; they are not used as identity keys. The len
   result adds runtime make_num traffic; do not assert zero allocations here.
5. Enable ordinary collection; create a list self-cycle with list_append, drop
   its external ref, then invoke gc_collect_cycles. Verify reclaimed nodes
   increases, and allocations of the live control object remain accessible.
   Run the existing pinned runtime cycle-collector tests against this build
   before using collection counts as evidence. Fresh process isolation avoids
   mixing pre-existing boot candidates with a fixed reclaimed-node assertion.
6. Parse the report: ordinary number request partition equality must hold;
   histogram calls sum to dispatch_builtin_calls; started collections equal
   completed + accounting-aborted collections on a successful normal exit.

Plant a temporary duplicate increment at list_append_owned: control 2 should
reject requests 18 instead of 9. Remove the make_num reuse increment: control 1
must fail its partition/reuse assertion. Remove histogram increment: control 4
and the histogram partition must fail. These are diagnostic counter controls,
not solver correctness certification.

## Coverage limits

Only single-threaded EMS is covered; counters are deliberately non-atomic.
Number metrics cover make_num and make_num_permanent constructor paths, not
every possible direct allocation of a VAL_NUM in other runtime files.
List creates cover make_list and make_list_heap. Append/growth counts cover
list_append and wrappers reaching it; direct list slot population and arbitrary
builtin resize paths are excluded. Do not label those counts all list writes.
GC scanned nodes count the unique universe after its reachability expansion,
not repeated passes or edges. Reclaimed nodes are the collector's garbage
count, excluding subsequent transitive refcount frees and excluding bytes.
Dispatch covers aot_call_dispatch only, using actual BuiltinFn equality.
Direct AOT builtin calls and VM-internal dispatch bypass this hook.
No source line attribution is inferred from g_trace_current_line.

No GC-suppression probe was prepared: safely bounding accumulation and handling
the pinned collector's registration pins is a separate experiment. The current
instrumentation is sufficient to measure scanning/reclamation yield first.
