Independent integration/evidence review by scalar_final_review, 2026-09-11.

All 75 timing samples and 40 consumer samples independently reproduce the
summaries. The 4x5 headline is 11.4927% less time; candidate/native is 7.0510x.
All 926 copy-map entries match their banked bytes, original bytes, sizes and
hashes, without duplicate destinations. All 115 sample/consumer records agree
with their retained process and validation records; counter CSVs and RSS agree.

Eight checker records return zero; all four proof pairs are byte-identical.
The candidate core patch matches the compiler/header diff. Focused source
hashes match the actual source; live VM/native/checker/AOT binaries and both
generated EMS C hashes match the manifest. All nine harness-commit files match
EigenMiniSat 58045ac.

One wording correction was requested and applied: the collector counter sums
universe-node visits across collections, counting each node once within each
collection. It does not report globally distinct nodes across the whole run.

No material numeric/provenance error or missing measurement evidence found.
This review used reads, arithmetic and hashes only; no builds, tests or solvers.
Full compiler-gate validation was pending and is separate evidence.
