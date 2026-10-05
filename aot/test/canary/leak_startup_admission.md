# Nine explicitly admitted startup allocations

Each of the five existing leak allowances increases by exactly nine. Slack remains eight; fixture population, leak.sh, slot companion and their comparators are unchanged. This admits measured new startup builtin Values. It is neither a retained-allocation fix nor a measured historical pin-to-current shutdown-floor delta.

The selected compatibility runtime is EigenScript `96bbbb543fdbfe01e809eee09e7c67986085ed1d`. Its `src/builtins.c` SHA256 is `20d0f1be766a37456107e4a8143abdc606297d9ae48a2214d613f3a20846961a`. Source comparison against the published runtime pin `a6c50fba6a6250ea347a34500d6c9fa503a5c931` identifies these nine added registrations:

| Registration identity | Implementation | Current source line |
|---|---|---:|
| `EIGS_FSTR_CONV_NAME` (reserved f-string conversion) | `builtin_str` |6619|
| `set_observer_window` | `builtin_set_observer_window` |6625|
| `get_observer_window` | `builtin_get_observer_window` |6626|
| `set_observer_scale` | `builtin_set_observer_scale` |6627|
| `get_observer_scale` | `builtin_get_observer_scale` |6628|
| `matmul_at` | `builtin_tensor_matmul_at` |6690|
| `matmul_bt` | `builtin_tensor_matmul_bt` |6691|
| `scatter_add` | `builtin_tensor_scatter_add` |6692|
| `task_sched_trace` | `builtin_task_sched_trace` |6761|

Every one of these nine identities has one measured80-byte `make_builtin` allocation stack in **each** of the five current-runtime reports: nine allocations/720bytes admitted per fixture. These are part of the startup population; they are not fixture-body allocation sites. The builtin Env layer shares Values after registration rather than creating another builtin population. The additional AOT task_spawn replacement is not one of the nine admitted identities.

Before this admission, the actual unchanged gate completed rc1: l1/l2/l5 failed with263/263/262 allocations; l3/l4 passed with262/263. Existing slot companion passed with both completion witnesses and262 allocations in each arm. The five reports account for1,313 reported80-byte allocations:1,308 builtin registrations plus five AOT task_spawn replacements. l3/l5 omit the registered print Value relative to the other reports; that absence does not prove destruction or explain historical pin counts.

Evidence is retained under `/home/jon/src/wt/release-blockers-20261003/aot-normal-leak-soft-trace-v2-results/`: `stage.stdout`, complete `stage.xtrace` SHA256 `9dd381491c57c0da9553ea79a23d88fdb322e1fe1c066588be9d3d6dda4b2ed1`, five `captured-reports/*.combined.txt`, per-block source identities in `allocation-source-map.json`, and `REPORT.md`. These are the gate's captured combined streams, with trailing newlines already removed by its original command substitution. Exact source/trace origins and hashes are retained; no shell text was evaluated while parsing.

Pinned historical raw allocation populations remain unmeasured. The old ledger numbers alone do not establish them; the nine-call source addition plus current raw stacks is the basis for this explicit admission. No broader memory-safety, zero-leak or growth claim follows. The prior failing result remains intact. The revised ledger still requires its own actual normal-stage verdict.
