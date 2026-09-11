# Integer-vector interpreter seam: next independent ceiling

No probe transpile, build, or runtime measurement has been performed.

The smallest useful probe is to splice **only the resolved pinned
`lib/int_vector.eigs`** through the existing static-loader branch, using pristine
ouroboros commit `328e7692be660b0f56ff73f752207fb25474101c`. Do not combine it with
the scalar compiler change. `prepare_stdlib_probe.py` generates precisely that
compiler variant plus baseline frontend/header copies, with exact replacement
assertions and source hashes. Its other change makes the compiler's own frontend
load absolute so the scratch compiler can run outside the project directory.

The current `splice_static_loads` branch leaves `lr[1] == "stdlib"` interpreted.
F-OURO-37 records the resolution contract; in a spliced child the runtime literal
is rewritten to an absolute path to preserve the child's resolution base.
This probe changes neither resolver nor nested-load policy, and retains the
existing top-level-return refusal. It admits one canonical resolved library
path into the existing parser/splicer.

## Evidence for this candidate

The pristine generated EMS C still dynamically resolves `int_vector_filled`
when constructing per-conflict `seen` (`ems-baseline.c:5508`) and
`int_vector_fill` before clause minimization (`:6150`); state construction has
additional setup calls. Corresponding solver locations are
`lib/solver.eigs:1607` and `:1971`. These library functions do floor/buffer/range
loops in the linked VM even though their callers are native.

The diagnostic run reports 19,964 AOT dispatches to VAL_FN, 166 collections,
345,851 universe-node visits (unique within each collection), and 298,128
collector garbage nodes.
Those counts motivate measurement; they do not attribute all interpretation or
collection to int_vector. Counts include shutdown and are diagnostic only.
The old profile's overlapping VM/GC shares cannot be added into a speed claim.

`text_builder.eigs` is only a comment-only compatibility shim: its operations
are already root builtins. Splicing that file would mostly remove a startup
load, not its solver operations, so leave it unchanged.

## Scheduled execution recipe

1. Generate with `python3 /tmp/ems-close-20260911/prepare_stdlib_probe.py`.
2. Run the pinned VM with argv: scratch `compile-int-vector.eigs`, original
   absolute EMS `minisat.eigs`, absolute runtime worktree. Save its exact stdout
   as `stdlib-probe/ems-int-vector-only.c`; require exit 0 and empty stderr.
3. Check native int_vector definitions/direct callsites, absence of its runtime
   load, retained text-builder shim load, and absence of scalar helpers.
   A compiler refusal or absent direct call is a failed probe preparation, not
   evidence of slow native code.
4. Compile with the baseline production GCC command, substituting this source
   and output binary. The generated source lives beside the **baseline** copied
   `aot_rt.h`, so quoted include selection cannot pick the scalar header.
   Link the original immutable release archive and retain exact argv/hash/logs.
5. Use the normal real correctness preflight, byte-exact EMS counters/output
   normalization, and proof checks before interleaved n=5 comparisons against
   pristine baseline and native MiniSat on identical inputs. Record hardware
   counters separately. Optionally repeat constructor/GC diagnostics to test
   whether the suspected work actually disappears; never time diagnostics.

## Eligibility limits before any production proposal

Splicing can change function-binding scope, later rebinding behavior,
source-file identity and nested load/eval semantics. The integer-vector source
uses bare local assignments (`vec`, `fill_value`, `out`), whose loaded-module
scope must not become caller-module mutation. All ten library definitions are
compiled by this probe, although EMS mostly calls two. Existing splicer
inference/refusal rules may reject one of the unused definitions; record that
result before deciding whether a narrower experiment is justified.

This is a scoped throughput experiment, not a general stdlib-admission patch.
No solver policy, vector algorithm, list/range implementation, or runtime
collector change is part of the candidate.
