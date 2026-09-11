The vector experiments use pristine compiler
`328e7692be660b0f56ff73f752207fb25474101c`, EMS solver source
`6d06b04336b96da83a26ac22d4a5b908aad52983`, and EigenScript v0.43.0 at
`a6c50fba6a6250ea347a34500d6c9fa503a5c931`. Their comparison runner is the
certificate implementation merged in EigenMiniSat #112 (`c5784a3`). All
recorded source snapshots and process commands are retained with each run.

The fresh scalar profile instead uses compiler implementation `7d6b6fa`,
merged as `6616fb7` in ouroboros #227. Its manifest records the exact compiler,
header, generated C, executable, input and reference hashes. Its sampled
`cycles:u` profile uses 199 Hz and DWARF call stacks; it is not timed evidence.

The completed 5×5 ladder uses that same scalar production build and the
certificate runner from EigenMiniSat #112. The execution recipe's `ladder-5x5`
stage sets individual solver/checker limits of 7,200 seconds and an overall
21,600-second limit. The runner requires both proof-off pilots to finish within
600 seconds before collecting five repeats. Its completed manifest records
50 validated samples over pigeonhole 6–5, simple SAT, and odd Tseitin 3×3,
4×4 and 5×5. Compiler source came from the clean authored `7d6b6fa` worktree;
the pinned runtime and source snapshots remained frozen throughout the run.

Use the [static-library preparation script](recipes/prepare_stdlib_probe.py)
or [bulk-fill preparation script](recipes/prepare_bulk_probe.py) to reconstruct
each isolated source variant. Their source-only manifests and exact GCC argv
are under each run's `preparation/`; actual build process records are under
`build/`. Both use the original fresh release archive with SHA256
`5db0ea14d1ce3476b03643622fea11359172075b21cdc6343d80a9e591bd6eca` and production
flags `-O3 -ffp-contract=off -falign-functions=64 -falign-jumps=32
-falign-loops=32 -march=native`. The quoted runtime-header include resolves to
the probe's frozen baseline header, keeping scalar-consumer changes excluded.

The [execution recipe](recipes/run_next_probes.py) records separately selected
build, comparison and profile stages. It invokes `compare_native.main` in the
same process, leaving solver/checker timeout and cancellation cleanup with
that runner. Do not wrap the runner in a second `run_process` session. The
bulk experiment checks its separately interpreted library hash before and
after the comparison; that file is also retained in its `preparation/`.

These scripts retain original absolute paths. The canonical compiler checkout
was still clean at `328e769` during both vector comparisons; it advanced to
`6616fb7` only after they completed. For a new run, substitute a clean checkout
of the recorded baseline, recreate its generated C and binaries, and use a
new output directory. Pointing the old baseline binary at today's compiler
checkout would give incorrect source provenance. Keep the runtime checkout
clean at the recorded pin and freeze source snapshots during each comparison.

The preserved pre-execution design note still says the library experiment has
not run; it is a historical preparation artifact. The completed manifests,
samples and checker results are the authority on execution status. No broad
stdlib-splicing or bulk-fill semantic guarantee follows from these EMS-only
measurements. Run one build, suite, profiler or timing job at a time on this
two-core development machine.
