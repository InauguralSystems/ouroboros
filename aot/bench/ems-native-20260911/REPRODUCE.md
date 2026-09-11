The recorded run used EigenMiniSat `6d06b04336b96da83a26ac22d4a5b908aad52983`,
baseline ouroboros `368603bf96a74382b16819d7bcb406d246343d89`, and clean
EigenScript `v0.43.0` (`a6c50fba6a6250ea347a34500d6c9fa503a5c931`). The candidate
is that compiler plus [candidate-core.patch](provenance/candidate-core.patch).
Both EMS arms contain the same active-learnt-count optimization; the solver
file SHA-256 is `ab3ab35e4c6c9e9036a4b2730c60b7f589e41c44ba7f69478a8a84c91607f7b8`.
The manifest records the native MiniSat package as `1:2.2.1-8build1`; its binary
hash, rather than package metadata alone, identifies the measured executable.

The original [manifest](manifest.json) and sample records retain absolute
machine paths for auditability. Paths below their original
`/tmp/ems-gap-20260911/wall-production/` prefix map directly to this bank.
The candidate worktree's changed file hashes and exact generated-C/archive
hashes are recorded separately in [candidate.json](provenance/candidate.json).
The complete baseline/EMS/runtime source census appears only in the original
manifest. The supplemental patch includes only the optimization's two core
files; test and gate changes do not enter the benchmarked program.
Two intermediate preflight path lists (`oracle/discovered` and `oracle/sorted`)
used NUL separators; their banked `.txt` counterparts use newlines. The
original hashes and mapping are in [bank-transforms.json](provenance/bank-transforms.json).
All actual sample outputs, timings, input CNFs, and references are unchanged.

To rebuild, use separate baseline and candidate compiler checkouts, the same
EMS checkout and pinned runtime, and one heavy job at a time. Replace these
absolute example paths with local paths:

```bash
export EIGS_DIR=/absolute/EigenScript-v0.43.0
export EIGS="$EIGS_DIR/src/eigenscript"
export EMS_DIR=/absolute/EigenMiniSat
export AOT_BASELINE=/absolute/ouroboros-baseline
export AOT_REPO=/absolute/ouroboros-candidate
export RUN_DIR=/absolute/new-measurement-work
export BANK="$AOT_REPO/aot/bench/ems-native-20260911"
mkdir -p "$RUN_DIR"
bash "$AOT_BASELINE/aot/build.sh" "$EMS_DIR/minisat.eigs" "$RUN_DIR/ems-baseline"
bash "$AOT_REPO/aot/build.sh" "$EMS_DIR/minisat.eigs" "$RUN_DIR/ems-candidate"
```

Those commands prepare the production runtime archives and binaries. The
measurement runner additionally requires the exact candidate C source; emit
and link it explicitly, as in the recorded candidate build:

```bash
"$EIGS" "$AOT_REPO/aot/compile.eigs" "$EMS_DIR/minisat.eigs" "$EIGS_DIR" > "$RUN_DIR/ems-candidate.c"
bash "$BANK/compile_c.sh" "$RUN_DIR/ems-candidate.c" "$RUN_DIR/ems-candidate" "$EMS_DIR"
```

[compile_c.sh](compile_c.sh) preserves the recorded production compiler flags
while taking checkout paths from the environment. It does not build the
archive. [compile_c.recorded.sh](provenance/compile_c.recorded.sh) is an exact
copy of the original script, including its original machine paths. GCC was
Ubuntu 13.3.0, with `-O3 -march=native -ffp-contract=off` and fixed function,
jump, and loop alignment (64/32/32 bytes). New host paths, runtime builds, or
compiler versions can change binary hashes; reproduce behavior and compare
new paired samples rather than expecting identical timings.

Run the EMS repository's measured-path harness with a **new** output directory:

```bash
python3 "$EMS_DIR/benchmarks/compare_native.py" \
  --aot-binary "$RUN_DIR/ems-baseline" \
  --aot-source-dir "$AOT_BASELINE" --runtime-dir "$EIGS_DIR" \
  --minisat-binary /usr/bin/minisat --output "$RUN_DIR/wall" \
  --build-command "bash $AOT_BASELINE/aot/build.sh $EMS_DIR/minisat.eigs $RUN_DIR/ems-baseline" \
  --candidate-binary "$RUN_DIR/ems-candidate" \
  --candidate-label 'production guarded slot-index local assignments' \
  --candidate-source "$RUN_DIR/ems-candidate.c" \
  --candidate-build-command "bash $BANK/compile_c.sh $RUN_DIR/ems-candidate.c $RUN_DIR/ems-candidate $EMS_DIR"
```

The script defaults to five repetitions, a 120-second per-process timeout,
and a 1,800-second total budget. It performs a fresh native/VM/baseline
correctness preflight, validates the candidate warmups, rotates the three
arms, and checks every measured output. The bank's exact input CNFs and
references are under [oracle](oracle/); [cases.json](cases.json) maps their
names and hashes. The measured run used the full affinity [0, 1], without
`--cpu`; no suites or builds ran beside it.

The instruction run uses DMG `ff29549873f625f99d78dd2e2c3cc884bc17d01a`,
`dmg.eigs roms/cpu_instrs.gb --cycles 50000000`, and the same two compiler
variants. [measure_instructions.py](instructions/measure_instructions.py)
is the exact saved recipe with **original machine paths**; adjust those paths
for a new machine. It alternates arm order, validates output, checks binary
identity, and records `perf stat -e instructions:u,cycles:u -x,` with at least
99% event running time. The DMG source/ROM/reference identities appear in
[instructions/provenance.json](instructions/provenance.json).

Targeted checker recipes copied under [provenance](provenance/) also retain
their original machine paths. They document the executed focused, independent
old-header sanitizer, and fault-plant checks. Durable regression enrollment
lives in `aot/test/run.sh`, `aot/test/leak.sh`, and their helper scripts. The
sanitizer evidence retains stdout witnesses, each original stderr hash and
summary, and the successful checker log; roughly 1 MB of repetitive existing
shutdown-allocation backtraces is intentionally omitted. The no-new-leak
verdict is relative to the original implementation, not an absolute zero.

No executables, runtime archives, ROM bytes, `perf.data`, or generated C are
included. The original provenance's `candidate-source.c` (1,377,971 bytes) is
omitted, while its exact SHA-256 remains in both provenance records. Empty
build transcripts are retained as recorded; they are not independent proof
of the declared source-to-binary relationship. Instruction/performance
results and the eventual full-gate verdict remain separate evidence.
