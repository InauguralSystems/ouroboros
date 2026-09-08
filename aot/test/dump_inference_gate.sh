#!/bin/bash
# --dump-inference AGREEMENT GATE (#126 follow-up).
#
# `--dump-inference` (#65) is the compiler's own storage report: one `D` record
# per name saying which storage class the emitter picked. Nothing tested it, and
# it drifted: after #126 made the observer bookkeeping PER NAME, three sites
# still keyed the reported kind off the whole-program `g_observed` flag, so an
# observed unit's un-named numerics were reported `observed-env` while the
# emitter gave them a plain C double. No value, verdict or exit code moved --
# only the instrument lied, which is the class of a comment that overclaims:
# the next reader believes it instead of re-deriving.
#
# So this gate joins the two artifacts of ONE transpile: the dump, and the C the
# same compiler emits for the same file. Cheap by construction -- two transpiles
# and a grep per fixture, no gcc.
#
# WHAT IT CHECKS (per `D <scope> <role> <name> <kind> <line>` record):
#   kind `double` / `long`  -> gen.c must contain NO observer storage call
#                              naming it, and MUST declare `<ctype> eig_<name>`
#                              (so a dump that says double while the emitter
#                              writes long is red too);
#   kind `observed-env`     -> gen.c MUST contain at least one observer storage
#                              call naming it.
# The observer storage class is exactly: aot_observe_num, aot_observe_val,
# aot_obs_reset_name, and the aot_get_*named* readers.
#
# WHAT IT DOES NOT CHECK (residual, stated so nobody reads more into a green):
#   - The other kinds. `boxed-env`, `boxed-shadow`, `boxed-env-for`, `buffer`,
#     `tensor` and `gen` records are counted and skipped: this gate is about the
#     observed/unobserved split #126 moved, not about the whole taxonomy.
#   - The `scope`, `role` and first-assignment `line` fields. Only `name` and
#     `kind` are adjudicated; a record filed under the wrong function, or with a
#     wrong line number, passes here.
#   - The grep is NAME-keyed and therefore SCOPE-BLIND: gen.c has no scopes to
#     match against. A fixture that used one name at two scopes with two
#     different kinds could not be adjudicated, so that is a loud FAIL below
#     rather than a silent pass -- keep the fixtures' names distinct per file.
#   - Observer QUERY sites (aot_report / aot_trajectory / aot_observe_of) are
#     not consulted; they read observer state rather than choose storage.
#   - Nothing here runs the program. Parity for these fixtures is the business
#     of the main tier in run.sh, which builds and diffs them against the VM.
#
# VACUITY: the fixture list is explicit (a missing file is a FAIL, never a
# skip), and every fixture must yield at least one checked `double`/`long`
# record AND at least one `observed-env` record -- a fixture that lost its
# observed name would otherwise pass by having nothing to disagree about.
set -uo pipefail
cd "$(dirname "$0")/.."
EIG="${EIGS:-../../EigenScript/src/eigenscript}"

# #126's own two fixtures, reused rather than duplicated: between them they
# cover all four quadrants of the split -- t297 has an observed MODULE global
# (`ch`) beside un-named module numerics, t298 has an observed function LOCAL
# (`w`) beside un-named parameters, un-named locals AND an un-named module
# global. Either one alone discriminates all three converted sites; the vacuity
# check below is what stops an edit to either from quietly emptying this gate.
FIXTURES="test/t297_obs_per_name_module.eigs test/t298_obs_per_name_function.eigs"

fail=0
n=0
for prog in $FIXTURES; do
  name=$(basename "$prog")
  if [ ! -f "$prog" ]; then
    echo "FAIL: dump-inference $name (fixture missing — the gate cannot be satisfied by absence)"
    fail=1; continue
  fi
  n=$((n + 1))
  if ! dump=$(timeout 120 "$EIG" compile.eigs "$prog" --dump-inference 2>/tmp/aot_dump_gate.log); then
    echo "FAIL: dump-inference $name (--dump-inference transpile failed)"
    tail -3 /tmp/aot_dump_gate.log; fail=1; continue
  fi
  if ! gen=$(timeout 120 "$EIG" compile.eigs "$prog" 2>/tmp/aot_dump_gate.log); then
    echo "FAIL: dump-inference $name (plain transpile failed)"
    tail -3 /tmp/aot_dump_gate.log; fail=1; continue
  fi
  if PROG="$name" DUMP="$dump" GEN="$gen" python3 - <<'PY'
import os, re, sys

prog, dump, gen = os.environ['PROG'], os.environ['DUMP'], os.environ['GEN']
# The observer STORAGE helpers, each taking the env then the name as a string
# literal: `aot_observe_num(__eigs_g, "acc", ...)`.
OBS = r'(?:aot_observe_num|aot_observe_val|aot_obs_reset_name|aot_get_[A-Za-z0-9_]*named[A-Za-z0-9_]*)'
CTYPE = {'double': 'double', 'long': 'long'}

rows, bad = [], []
for line in dump.splitlines():
    if not line.startswith('D '):
        continue
    f = line.split()
    if len(f) != 6:
        bad.append('malformed record: ' + line)
        continue
    rows.append(tuple(f[1:5]))          # scope, role, name, kind

if not rows:
    print('  no D records at all — --dump-inference emitted nothing')
    sys.exit(1)

# scope-blind grep: one name may not carry two kinds in one file.
kinds = {}
for scope, role, nm, kind in rows:
    kinds.setdefault(nm, set()).add(kind)
for nm, ks in kinds.items():
    if len(ks) > 1:
        bad.append("name '%s' is reported with two kinds %s — the gen.c grep is "
                   "scope-blind and cannot adjudicate that" % (nm, sorted(ks)))

def observed_calls(nm):
    return re.findall(OBS + r'\(\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*"' + re.escape(nm) + r'"', gen)

n_plain = n_obs = n_skip = 0
for scope, role, nm, kind in rows:
    if kind in CTYPE:
        n_plain += 1
        hits = observed_calls(nm)
        if hits:
            bad.append("%s %s '%s' is reported %s but gen.c observes it by name: %s"
                       % (scope, role, nm, kind, sorted(set(hits))[0]))
        if not re.search(r'\b' + CTYPE[kind] + r'\s+eig_' + re.escape(nm) + r'\b', gen):
            bad.append("%s %s '%s' is reported %s but gen.c declares no `%s eig_%s`"
                       % (scope, role, nm, kind, CTYPE[kind], nm))
    elif kind == 'observed-env':
        n_obs += 1
        if not observed_calls(nm):
            bad.append("%s %s '%s' is reported observed-env but gen.c has no observer "
                       "storage call naming it" % (scope, role, nm))
    else:
        n_skip += 1

if n_plain == 0:
    bad.append('no double/long record — the fixture no longer exercises the un-observed half')
if n_obs == 0:
    bad.append('no observed-env record — the fixture no longer exercises the observed half')

for b in bad:
    print('  ' + b)
if bad:
    sys.exit(1)
print('  %d record(s): %d plain-C, %d observed-env, %d other kind(s) not checked'
      % (len(rows), n_plain, n_obs, n_skip))
PY
  then
    echo "PASS: dump-inference $name"
  else
    echo "FAIL: dump-inference $name (the dump disagrees with the C the same transpile emits)"
    fail=1
  fi
done

if [ "$n" -eq 0 ]; then
  echo "FAIL: dump-inference gate examined ZERO fixtures (the gate is vacuous)"
  exit 1
fi
[ "$fail" -eq 0 ] && echo "--- dump-inference gate: $n fixture(s) joined against their own gen.c ---"
exit "$fail"
