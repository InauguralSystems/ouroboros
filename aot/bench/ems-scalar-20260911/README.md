Scalar consumers of lowered frame locals reduce the regime-C 4×5 odd-Tseitin
production AOT median from **13.723669 s to 12.146443 s** (11.49% less process
wall time, five runs per arm). Native MiniSat's paired median is 1.722651 s,
so the observed ratio narrows from 7.97× to **7.05×**. The target of at most
2× native on every rung through 6×6 remains open. Native and EMS use different
search policies; their wall ratio does not isolate language overhead.

The compiler specializes equality conditions and indexed assignments whose
index is a sum of two lowered slots. Numeric reads avoid temporary heap values;
other values retain the existing helper behavior. Eligibility follows actual
storage lowering, excludes calls/observation/tracing, and restricts equality's
right operand to nonraising atoms. Reads retain source order, numeric
materialization boundaries, and heap-NaN identity. The production EMS C reaches
all six propagation sites selected by the earlier isolated probe. EMS search
code, policies, and the runtime ABI are unchanged.

Process wall seconds, median [minimum–maximum], five samples per cell:

| Case | Evidence | Baseline AOT | Candidate AOT | Native MiniSat |
|---|---|---:|---:|---:|
| Pigeonhole 6–5 | VM + certificate | 0.073620 [0.065789–0.081758] | 0.066324 [0.061369–0.074939] | 0.007774 [0.007369–0.009796] |
| Simple SAT | VM + model | 0.005130 [0.005018–0.005340] | 0.007633 [0.005077–0.016134] | 0.007127 [0.006269–0.008161] |
| Odd Tseitin 3×3 | VM + certificate | 0.207896 [0.195436–0.226795] | 0.190657 [0.176908–0.200374] | 0.010798 [0.009009–0.011524] |
| Odd Tseitin 4×4 | VM + certificate | 8.996804 [8.819667–9.324852] | 8.064721 [7.960460–9.472084] | 0.254846 [0.245155–0.264589] |
| Odd Tseitin 4×5 | Certificate | 13.723669 [13.393180–14.331190] | 12.146443 [12.011066–13.311551] | 1.722651 [1.688483–1.775598] |

Every EMS row uses **CDCL current defaults, regime C, no overrides**; every
native row uses executable defaults. Conflicts remain 592/9,986/12,787 on the
three tori. All 75 interleaved samples validated. The 4×5 distributions do not
overlap; the short-case differences are unresolved. One 4×4 candidate outlier
overlaps the baseline range, so that first run alone does not establish its
speed improvement. Its descriptive paired AOT/native ratio is 31.65×.

A separate five-repeat baseline/candidate wall run confirms the 4×4 gain:
**9.281391 [9.025475–9.419787] → 8.160491 [8.099941–8.271736] s**, or 12.08%
less time, with nonoverlapping ranges. This run measures the complete solver
invocation under `/usr/bin/time`; it contains no native MiniSat arm, so its
times are not mixed into the native comparison above.

Separate user-mode hardware-counter measurements, five runs per arm:

| Workload | Baseline instructions | Candidate instructions | Change | Baseline hardware cycles | Candidate hardware cycles |
|---|---:|---:|---:|---:|---:|
| EMS 4×4, regime C | 16,377,664,462 | 14,314,999,223 | −12.59% | 18,454,422,864 | 16,708,900,836 |
| DMG, CPU instruction ROM | 12,496,188,682 | 12,496,041,448 | −0.00118% | 6,830,420,048 | 6,602,124,645 |

DMG completes exactly 50,000,008 emulated cycles in every run. Its ranking
metric is essentially flat: **249.923734 → 249.920789 host instructions per
emulated cycle**, within the 2% regression limit. Hardware cycles fell 3.34%.
The independent DMG wall medians are 3.238766 → 3.070672 s, with overlapping
ranges (3.164917–3.419582 versus 3.040705–3.539585); the wall effect remains
unresolved and no wall regression over 5% is confirmed. Unprofiled median peak
process-tree RSS stays at 86,400 KiB for EMS and 6,600 KiB for DMG, within the
10% growth limit. The counter wrapper's DMG RSS includes `perf` and is higher;
it is retained separately and is not reported as solver heap usage.

[timing](timing/) retains the original timing manifest, all sample output and
validation records, inputs, proofs, checker results, and explicit per-case
evidence labels. The new certificate mode landed in
[EigenMiniSat #112](https://github.com/InauguralSystems/EigenMiniSat/pull/112).
It kept both mandatory VM anchors, checked eight UNSAT proofs by drat-trim's
exit status, checked both AOT SAT models, and compared every proof-off timed
output with its certified reference. All four baseline/candidate proof pairs
were byte-identical. The 4×5 point has certificate evidence, not a VM parity
claim. All extra-rung pilots finished within 600 seconds; individual solver
and checker caps were 7,200 seconds.

The initial investigation is retained in [baseline-investigation](baseline-investigation/).
An isolated range-removal probe had overlapping wall ranges; its 4×4 instruction
median fell from 16.378B to 15.483B. The independent six-site scalar probe had
separated 4×4 wall ranges (9.306915 → 8.553618 s) and 14.694B instructions.
These were timing experiments, not general compiler-correctness claims.
The production measurements above are separate.

Controlled diagnostic instrumentation counted 51,871,510 numeric constructions
(49,427,755 freelist reuses), 3,205,672 list constructions and 11,957,687
appends in baseline EMS. All 166 collections completed, recording 345,851
universe-node visits (unique within each collection) and reclaiming 298,128
nodes. That result does not support
skipping allegedly unproductive collection. Dynamic dispatch counted 2,877,875
calls to `len`, 2,153,041 to `append`, 562,498 to `range`, and 19,964 interpreted
function calls. The controls rejected a missing freelist increment; diagnostic
output matched the VM reference. These counters were absent from timing builds.
The baseline profile's overlapping propagation/collection/dispatch shares must
not be added to estimate another optimization's gain.

Focused validation completed 12/12 VM/AOT pairs, six existing and six new
emission fixtures, and 38 scalar-emission selftest cases. Independent static
review found no established remaining defect. The certificate runner passed
61 tests; the complete original oracle selftest caught 68 planted failures,
passed three clean controls, witnessed all 49 production failure sites, and
skipped none.

The local compiler driver passed 344 AOT fixtures, 12 benchmark builds,
64 build-refusal cases, one runtime-refusal case, 63 self-host programs plus
bootstrap, the exact DMG canary, the 70/77 stdlib sweep, the 157/230 corpus
comparison (unchanged ledger, two NONDET exclusions), and the 20-translation-unit
core check. Its [original verdict](validation/seven-tier-gate.log) remains
`aot=0 selfhost=0 canary=0 stdlib=0 corpus=0 leak=1 core=0`, followed by
`TIERS_DONE`: LeakSanitizer could not run under the sandbox's ptrace setup.
The strict ownership gate rejected its incomplete report. The five legacy
rows reporting zero allocations in that failed attempt are invalid evidence.

The normal [leak tier rerun outside the sandbox](validation/leak-escalated.log)
passed with exit 0. The five fixtures reported 253/253/253/253/252 allocations
under the existing ledger policy; these are existing shutdown leaks, not a
zero-leak claim. The strict expanded ownership A/B reported 253/253, with both
completion witnesses and no allocation slack. Seven actual scalar helper
faults were rejected, covering materialization boundaries, heap-NaN identity,
and both owned-value releases. The actual production emission-gate CLI also
rejected the pristine compiler with exit 1 and the expected missing-helper
diagnostic; [its manifest](validation/scalar-old-emitter-cli/manifest.json)
and [the validation summary](validation/scalar-validation-summary.json) retain
the evidence. The failed driver attempt and successful
sanitizer retry are retained separately rather than rewriting the original
verdict.

[REPRODUCE.md](REPRODUCE.md) records source identities, recipes and scope.
[copy-map.json](copy-map.json) maps original absolute artifact paths to banked
copies with their unchanged hashes. Executables, generated C, large perf data,
and diagnostic build products are not included. They can be rebuilt from the
recorded source revisions, core patch and commands. This bank does not add a
completed 5×5, 5×6 or 6×6 performance point.
