Scalar production completed a certified odd-Tseitin 5×5 comparison:
**302.121645 seconds** median for EigenMiniSat versus **8.851535 seconds** for
native MiniSat, a **34.1321×** ratio. Both arms completed five repetitions;
the whole five-case run validated 50 samples. The ≤2× target remains open.

| 5×5 arm | Process wall seconds, median [min–max] | Conflicts |
|---|---:|---:|
| Scalar production AOT | 302.121645 [291.904933–317.722734] | 87,981 |
| Native MiniSat | 8.851535 [8.685464–9.495678] | 2,236,075 |

The AOT proof-producing solve took 316.241568 seconds; `drat-trim` verified
its refutation with exit 0 in 10.928268 seconds. Proof-off pilots of 304.238311
and 9.419841 seconds met the ≤600-second requirement before repetition began.
Certification and pilots are excluded from the five-repeat medians. The 5×5
row uses certificate validation; the mandatory smaller cases additionally
retain the VM differential oracle. Search policies differ between the two
solvers, so their ratio does not isolate language overhead. See the
[complete ladder table](scalar-ladder-5x5/summary.tsv) and its raw evidence.

Two earlier independent integer-vector experiments completed their correctness and
timing checks, but their 4×4 timing ranges overlap. Neither currently supports
a production performance claim. Both start from compiler `328e769`, before
the scalar-consumer change in [ouroboros #227](https://github.com/InauguralSystems/ouroboros/pull/227).
They are separate experiments; their results must not be combined with that
change or compared against each other's baseline.

Each row uses regime-C EigenMiniSat, `--cdcl`, no policy overrides, and five
process-wall samples per arm:

| Independent experiment | Baseline seconds, median [min–max] | Probe seconds, median [min–max] | Descriptive median change |
|---|---:|---:|---:|
| Compile the pinned integer-vector library | 8.781040 [8.132010–9.658058] | 8.067980 [7.113689–8.630197] | −8.12% |
| Use existing bulk fill; keep library interpreted | 9.404535 [8.871027–9.954748] | 8.942639 [8.676719–9.309051] | −4.91% |

Each comparison retained native MiniSat as its own third arm, the VM anchors,
pigeonhole and SAT controls, and certificate validation. Each completed 60
validated samples across four cases, verified six DRAT proofs and two AOT SAT
models. All three baseline/probe proof pairs in each experiment are
byte-identical. The three UNSAT cases are pigeonhole 6–5 and odd Tseitin 3×3
and 4×4; their EMS conflict counts remain 140, 592 and 9,986. Native uses its
own default policy, so a wall ratio does not isolate language overhead.
Full per-case results are in [the library compilation run](vector-stdlib/summary.tsv)
and [the bulk-fill run](vector-bulk/summary.tsv).

The first probe admits only the resolved pinned `lib/int_vector.eigs` through
the compiler's existing static-load splicer. Emission checks require native
definitions/direct calls, removal of its runtime load, retention of the
absolute `text_builder.eigs` shim load, and absence of the scalar-consumer
helpers. The second changes only two integer-vector fill loops to existing
`buf_fill` calls and redirects one baseline generated-C library path to the
isolated modified file. Both integer-vector functions remain interpreted in
that experiment. All other EMS search code stays unchanged.

These are timing experiments with limited correctness evidence. Static review
identified additional requirements for a general implementation: the old
`range` loop rejects lengths above one million while buffers permit ten
million; `int_vector_fill` currently accepts generic lists and has distinct
empty-input behavior; dynamic builtin rebinding and temporal loop-variable
history can observe the implementation change. Static library splicing also
needs to preserve the loaded-module boundary for assignments to names such as
`vec`, `fill_value` and `out`, including caller-name collisions. No general
library or loader change is promoted by this bank.

A separate [fresh profile of scalar production](scalar-profile/scalar-production.perf.report)
uses the exact build from #227, not either vector probe. Its output matched the
pinned VM reference and its source/header/C/binary/input hashes were checked
before and after. Inclusive sampled shares are propagation 48.02%, collection
18.79%, generic dispatch 18.44%, numeric construction 8.28%, and scalar indexing
6.78%. These shares overlap and must not be added to predict another speedup.
Profile timing is diagnostic and is not a production timing sample.

[REPRODUCE.md](REPRODUCE.md) records identities and invocation limits.
[copy-map.json](copy-map.json) maps 1,235 original artifacts to unchanged copies
with SHA256 and byte counts. Executables, generated C and raw `perf.data` stay
outside this bank; scripts, source revisions and hashes record how to rebuild
them. No completed result for 5×6 or 6×6 is included in this bank yet.
