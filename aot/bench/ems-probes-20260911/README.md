Collection-local traversal reuse reduced odd-Tseitin 4×5 median wall time by
**4.04%**, with separated observed ranges. This is a runtime-overlay experiment
against EigenScript v0.43.0, using the same scalar-production generated C in
both arms. It supports this workload result; broader runtime promotion still
requires validation and measurement against the runtime's current main.

| Odd Tseitin case | GC baseline seconds, median [min–max] | Traversal reuse seconds, median [min–max] |
|---|---:|---:|
| 4×4 | 8.482075 [7.953685–8.673857] | 7.864493 [7.653334–10.020376] |
| 4×5 | 12.225581 [11.984297–13.610266] | 11.731892 [11.627065–11.856999] |

The 4×4 ranges overlap, so its descriptive −7.28% median change remains
unresolved. Both rows use n=5 process-wall samples with diagnostics excluded.
The [complete GC comparison](gc/comparison/summary.tsv) retains native MiniSat
as its third arm. It completed 75 validated samples, eight AOT DRAT checker
successes, four byte-identical baseline/candidate proof pairs, and two valid
AOT SAT models. Independent review recomputed all 15 aggregates and verified
input, proof, model, binary, runtime-overlay and frozen-source bindings.

The candidate counts internal ownership edges during universe discovery and
records whether each node has a traversable child. Root marking skips the
child scan only when that collection-local fact is false. The original edge
table, duplicate-edge accounting, triggers, pins, root test and reclamation
remain unchanged. The extra byte array is local to a collection and does not
change the public Value layout.

A separate instrumented 4×4 run preserved all 166 collection records exactly:
345,851 total node visits and 298,128 reclaimed nodes. It removed 5,240,954
counting-slot visits and 2,672,348 marking-slot visits, with 47,181 no-child
skips. Those node totals sum unique nodes within each collection; they are not
counts of unique allocations across the process. This workload's measured
env/chunk traversal counts are zero. Controls preserved live numeric lists
and duplicate ownership edges; dropping duplicate counts failed reclamation,
and suppressing the skip metric kept output correct but failed the validator.
These targeted checks do not replace the full runtime and sanitizer suites.

The independent guarded `len` dispatch experiment reaches the real EMS workload and
preserves its output, counters and proofs. Its timing effect remains unresolved:
both measured ranges overlap, so no production change is promoted.

| Odd Tseitin case | Scalar baseline seconds, median [min–max] | Len probe seconds, median [min–max] |
|---|---:|---:|
| 4×4 | 8.366425 [7.303463–8.779503] | 7.719216 [6.815986–8.822398] |
| 4×5 | 12.185381 [10.894813–12.579546] | 11.967093 [10.476638–12.704916] |

The descriptive median changes are −7.74% and −1.79%. These are five
process-wall samples per arm, with diagnostics excluded. Native MiniSat is a
separate third arm in the [complete results](len/comparison/summary.tsv).
Solver policy remains regime C. This experiment is independent of the earlier
integer-vector probes and starts from scalar production in ouroboros #227.

The probe changes exactly 211 named `len` dispatch sites in the generated C.
It guards the actual `builtin_len` function identity and LIST/BUFFER argument
types, then calls the original builtin and preserves its boxed result,
post-call checks and ownership. It omits the generic dispatcher's search for
a borrowed list element: `builtin_len` returns a newly owned number. Other
callees and argument types fall back to the original dispatcher. Unary `len`
does not allocate an argument list; this experiment does not claim to remove
one. Reversing the replacements restores the original C byte-for-byte.

The real 4×4 diagnostic recorded 2,877,875 entries: 2,875,964 eligible calls
and 1,911 fallbacks. Its output matches the VM after timing normalization.
Controls verify two eligible and two fallback calls; forced fallback keeps
all four outputs correct but makes the real validator reject zero reach.
Separate preparation controls accept the pristine inventory and reject a
candidate-only flag change and a rewritten inventory. Timing builds exclude
both diagnostic and control macros.

The comparison completed 75 validated samples across five cases and three
arms. Eight AOT UNSAT proofs passed `drat-trim` with exit 0, four baseline/probe
proof pairs are byte-identical, and two AOT SAT models satisfy every clause.
Small cases retain VM differential validation; 4×5 uses certificates.
Independent artifact review recomputed every timing aggregate and checked
all proof/model, source, binary and diagnostic bindings.

The scalar-production 5×6 certificate attempt hit the separately imposed
3 GiB address-space bound. The AOT process reported out of memory and exited
with SIGABRT; its recorded 945.905 seconds includes stalled crash-dump handling
and is not a completed solver timing. Native MiniSat had returned UNSAT in
35.336 seconds during certification. No AOT proof, checker, proof-off pilot or
n=5 sample completed for this rung. The [failed attempt](ladder/5x6/failure.json)
and [resource limits](ladder/5x6/limits.json) are retained. This is a resource
failure, not a semantic disagreement or an established performance ratio.
Memory attribution is tracked in
[ouroboros #229](https://github.com/InauguralSystems/ouroboros/issues/229).

The [6×6 attempt](ladder/6x6/failure.json) failed at the same address-space bound:
the AOT process reported a failed 64-byte allocation and exited with SIGABRT
after 644.284 seconds. Native certification had returned UNSAT in 260.848
seconds. There is no completed AOT certificate, checker, pilot or n=5 sample
for this rung either. This attempt used the Linux one-byte core-limit sentinel,
verified by a small abort control, to avoid repeating the stalled crash-dump
path. Both resource wrappers restored their original limits and verified their
driver identities on exit.

[REPRODUCE.md](REPRODUCE.md) records the reconstruction boundaries.
[copy-map.json](copy-map.json) maps 1,547 original artifacts to unchanged copies
with hashes and sizes. The ≤2× native target remains open; no completed 5×6 or
6×6 timing point is established here.
