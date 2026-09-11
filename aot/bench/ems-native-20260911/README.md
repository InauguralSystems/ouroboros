The guarded slot-index compiler change reduces the 4×4 odd-Tseitin production
AOT median from **10.230163098 s to 9.197103458 s** (10.10% less process wall
time). Native MiniSat's median is **0.264683674 s**, so the observed wall ratio
narrows from **38.65× to 34.75×**. This remains a substantial gap. Native uses
different search policies and executes different conflict counts; that ratio
does not isolate language overhead.

The change fuses an indexed read and a lowered-local assignment, avoiding
numeric boxing and target refcount traffic. It admits only actual lowered
frame-local targets with call-free operands; runtime tags select the list or
buffer path, and existing helpers handle other values and errors. Production
emission reaches 19 sites, including all nine in watched CDCL propagation.
The earlier active-learnt scan optimization is present in both measured arms;
this comparison attributes the additional slot-index change.

Process wall seconds, median [minimum–maximum], five samples per cell:

| Case | Policies: EMS / native | Baseline AOT | Candidate AOT | Native MiniSat |
|---|---|---:|---:|---:|
| Pigeonhole 6–5 | regime C / native defaults | 0.076632 [0.068386–0.079141] | 0.082294 [0.069274–0.088936] | 0.007588 [0.007281–0.009134] |
| Odd Tseitin 3×3 | regime C / native defaults | 0.210572 [0.208455–0.220471] | 0.196882 [0.188027–0.227051] | 0.008962 [0.008658–0.009059] |
| Odd Tseitin 4×4 | regime C / native defaults | 10.230163 [10.108418–10.719214] | 9.197103 [9.091111–9.543052] | 0.264684 [0.256480–0.274516] |

The short-case ranges overlap; their speed differences are unresolved. The
4×4 ranges do not overlap in this run. EMS uses `--cdcl` with current defaults
and no policy overrides. All 45 measured outputs were validated against the
fresh preflight references; EMS agrees byte-for-byte with the pinned VM after
removing only its trailing numeric `ms` field. Its 4×4 conflict count remains
9,986; native MiniSat records 84,150.

The run began 2026-09-11 06:13:38 UTC on a two-core Intel Celeron N3350, Linux
x86-64, with affinity [0, 1], `LC_ALL=C`, and one heavy job at a time. Five
repetitions rotate arm order by repetition and case: 3 cases × 3 arms × 5 =
45 sequential, interleaved measurements. Each case has an unmeasured native,
VM, and baseline correctness preflight plus a candidate warmup. Wall time
includes process launch, parsing, solving, printing, and exit; it excludes
preflight and validation. [summary.tsv](summary.tsv) retains full precision;
[samples.jsonl](samples.jsonl) retains order and timings, and [samples](samples/)
retains each process's raw output and validation record.

A separate interleaved `perf stat` run has 20 validated measurements: two
programs × two arms × five. These are user-mode hardware events:

| Workload and metric | Baseline median [min–max] | Candidate median [min–max] | Median change |
|---|---:|---:|---:|
| EMS 4×4, regime C: instructions | 17,990,521,397 [17,990,372,272–17,990,621,466] | 16,377,520,679 [16,377,425,827–16,377,627,381] | −8.97% |
| EMS 4×4, regime C: hardware cycles | 21,182,298,598 [20,816,708,700–21,386,923,689] | 18,748,796,753 [18,527,807,060–18,833,584,193] | −11.49% |
| DMG CPU instruction ROM: instructions | 12,494,625,548 [12,494,621,192–12,494,631,086] | 12,496,190,964 [12,496,187,211–12,496,191,411] | +0.01253% |
| DMG CPU instruction ROM: hardware cycles | 6,653,637,829 [6,644,054,307–6,708,090,370] | 6,856,737,672 [6,845,537,169–6,869,634,090] | +3.05% |

DMG completes exactly 50,000,008 emulated cycles in every run. Its ranking
metric changes from 249.89247 to 249.92378 host instructions per emulated
cycle, essentially flat. Its hardware-cycle increase is a measured regression
in this run; the instruction result does **not** establish unchanged runtime.
[instructions](instructions/) contains all counters, outputs, and the exact
measurement recipe with its original machine paths.

The final production [user-mode profile](profile/production-user.report) has
1,786 samples and zero lost samples. Remaining inclusive shares include
watched CDCL propagation 52.29%, cycle collection 17.84%, generic call dispatch
17.36%, literal redundancy analysis 11.23%, generic index reads 9.62%,
`make_num` 8.66%, `val_incref` 7.60%, and `range` 6.05%. These shares overlap
and must not be summed or compared with earlier percentages as absolute
cost. About 4.47% remains unresolved. The exact
`perf record -e cycles:u -F 199 --call-graph dwarf` command, input and binary
identities are retained in [profile](profile/); this is a single diagnostic
profile, separate from the five-repeat measurements.

Completed validation evidence in [validation](validation/):

- Six focused VM/AOT pairs and six emission checks pass. The main fixture
  reaches all three helpers; callback, traced, and observed controls reach none.
- ASan A/B against the original header: direct C 253/253 leaked allocations;
  generated ownership fixture 253/252. Witnesses agree and neither comparison
  adds a sanitizer error. These are no-new-leak results, not leak-free results.
  The enrolled C gate separately passes at 253/253, with no slack.
- Six actual helper faults are rejected: missing element retain, missing old
  destination release, extra heap-number guard, missing buffer guard, and
  acceptance of fractional list or buffer indices.
- Nine UNSAT proof checks pass, a truncated proof is rejected, and the SAT
  control writes no proof. Old/new 3×3 proofs are byte-identical. Differential
  fuzz checks 200 instances with 84 verified proofs and zero failures; policy
  fuzz checks 240 solves over 40 instances and six policies, with 138 verified
  proofs and zero failures. Reduction occurs in 60 solves and compaction,
  watch rebuilding, and replay each occur in 33.
- Independent optimizer and integration reviews report no remaining concrete
  issue. The integration review found a missing durable emission check; the
  enrolled six-fixture check now rejects the old compiler (all three helper
  counts zero) and passes the candidate. It also independently recomputed all
  wall-time and instruction summaries. See [optimizer review](validation/critic-final.md)
  and [integration review](validation/integration-review.md).
- The restored DMG canary reference is independently reproduced by the pinned
  VM and pre-change AOT binary; [raw outputs](validation/dmg-reference-check/)
  are retained, with source, ROM, and binary hashes in instruction provenance.

All seven local tiers completed with exit 0 against the clean pinned runtime:

- Core: 20 translation units match the runtime build inventory.
- AOT: 338 main fixtures, 12 benchmark builds, 64 compile refusals, and one
  runtime refusal pass; the emission and gate selftests are enrolled.
- Self-hosting: 63 programs plus the byte-exact bootstrap and its execution pass.
- DMG: output matches the independently verified 50-million-cycle reference.
- Standard library: 70/77 modules build; the seven failures match the baseline.
- Corpus: 157/230 byte-exact matches; the remaining outcomes match the existing
  ledger, with two nondeterministic tests excluded. The log contains one process
  crash. This ledger-based tier does not establish that every corpus program
  is correct or crash-free, and no ledger was changed in this round.
- Leak tier: five fixtures pass at 253/253/253/253/252 allocations; the added
  ownership A/B passes at 253/253 with two completion witnesses and no slack.

The exact verdict, populations, and log are retained in
[seven-tier-verdict.json](validation/seven-tier-verdict.json) and
[seven-tier-gate.log](validation/seven-tier-gate.log). PR and post-merge CI are
recorded by GitHub separately from this local validation.

[manifest.json](manifest.json) is the original, unmodified timing manifest,
including baseline/compiler/runtime/EMS source hashes, binary hashes, host
details, policies, and recorded commands. [provenance/candidate.json](provenance/candidate.json)
adds the candidate's changed source hashes and core patch because the original
manifest's compiler snapshot describes the baseline. No binaries or large
generated C are banked. [REPRODUCE.md](REPRODUCE.md) explains source identities,
portable build commands, original paths, and the intentionally omitted files.
