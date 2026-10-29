# Evidence: ouroboros #351 / #267

## Sources and RED

Starting commit: `8b73faf5665d333c9fdd81f065648727c26d53cc`.

Read both issue bodies in full with the GitHub API.  The pinned EigenScript source
is commit `c5630ffb6a6a9249039162609f316638e9b477cc`, derived from the single
`EIGS_REF` in `.devcontainer/Dockerfile`, not from memory.  Source census:

```
rg -n "INTERROGATE_NAMED|trace_query_at|interrogative" "$PIN/src" "$PIN/docs"
```

`docs/GRAMMAR.md` derives exactly six question words: `what`, `who`, `when`,
`where`, `why`, `how`; `prev` is the seventh interrogate kind.  `src/compiler.c`
shows bare identifiers compile named for who/when/where/why/how (plus prev),
while what uses the value opcode.  `src/vm.c` lines 5560-5654 establish the
unqualified contract: what returns the number/bool itself, string/list length,
and zero for dict; who returns the binding name for an identifier (the type name
for value-form operands); when returns the binding assignment count; where is
current entropy; why is dH; how is settledness(dH).  No `at` form is involved.
`docs/SPEC.md` corroborates the live binding contract and assignment counting.

The base AOT stopped these forms at `aot/compile.eigs` with:

```
AOT: temporal interrogative without `at` only supports `prev`
```

## Fix and differential fixture

`aot_interrogate_named` mirrors the two VM opcode arms, consumes the owned
subject exactly once, and reads named observer state without mutating it.  The
emitter now sends all six bare forms to it while retaining the existing prev
and `at` paths.  `aot/test/t480_bare_interrogatives.eigs` covers all six forms
for num, bool, str, list, and dict (30 cells); the ordinary AOT harness compares
stdout and exit status byte-for-byte.

```
EIGS_DIR="$PIN" EIGS="$PIN/src/eigenscript" bash aot/build.sh test/t480_bare_interrogatives.eigs /tmp/t480
"$PIN/src/eigenscript" aot/test/t480_bare_interrogatives.eigs >/tmp/t480.vm
/tmp/t480 >/tmp/t480.aot
diff -u /tmp/t480.vm /tmp/t480.aot
# exit 0
```

## Corpus ledger

The complete 294-program corpus run showed eight of #351's 14 rows now SAME and
they alone were removed: entropy_cycles, entropy_query_time, halting_descent,
halting_stall, interrogative_spec, observer_coherence, stable_band, temporal.
Six rows passed the old refusal but exposed independent follow-ups and remain in
the ledger with their measured reason:

* observer_interactions: DIVERGE, promoted-name `report` assertion OI-895;
* parameter_when_2016: DIVERGE, `[0,0,0,0,0]` vs `[1,2,3,13,3]` assignment counts;
* temporal_when and unobserved: REFUSE, unsupported `when <n>` occurrence qualifier;
* unobserved_neutral: DIVERGE, pre-existing unobserved trajectory neutrality failures;
* when_store_sites_2016: REFUSE, unsupported destructure statement emission.

The run also exposed unrelated baseline/environment movements (autograd and
dict_remove_scaling new; two GC rows and sandbox_budget improved); those rows
were not changed.  Thus no row was removed unless this fix made it SAME, and no
new waiver was added.

## No-interrogative Ir control

Built `aot/bench/activation_kernel.eigs` (contains no interrogative) from the
starting commit and candidate, both with `AOT_ARCH=-msse2`, then ran the complete
program under callgrind.  Output diffed exactly and total Ir was identical:

```
/tmp/cg-base-sse:summary: 54570010
/tmp/cg-new-sse:summary:  54570010
Delta: 0 Ir (0.000%)
```

The first native-architecture attempt encountered valgrind's unsupported host
instruction before the workload; it was discarded, and both controls were
rebuilt with the repository-supported `AOT_ARCH` override rather than treating
that partial count as a measurement.

## Checks

Command tails and final status are appended below before commit.

### Recorded tails

* Full CI command: exit 0. Tail: fuzz seed 297 generated 20, compared 20,
  divergences 0, run-pasts 0, AOT gaps 0, hangs 0.
* Requested fuzz seed 351: exit 0. Tail: generated/compared 20, divergences 0,
  run-pasts 0, AOT gaps 0, hangs 0; largest reduction distance 5 ULP (bound 32).
* AOT differential suite: `--- all AOT parity tests passed ---`, including
  `PASS: t480_bare_interrogatives.eigs`.
* Corpus differential: 294 programs examined; detailed row disposition above.
* `tools/selftests.sh` and `tools/enrolment_check.sh` do not exist in this
  repository (both commands exit 127); no suite section or self-test mode was
  added by this change.
