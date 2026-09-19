#!/bin/bash
# aot/k_oracle.sh -- does DMG run at the speed the AOT DECLARES it should?
#
# The previous oracle for this work was one-sided: "at least as fast as a
# real Game Boy." A threshold like that is satisfied forever the moment it
# is crossed, and DMG crossed it long ago (17.3 MHz against hardware's
# 4.19). It cannot say we are 2% of what this box can do, which is the
# actual situation. So the oracle declares a VALUE and diffs it.
#
# The declared quantity is K = host instructions retired per emulated cycle
# (aot/k_budget.txt), not MHz, because K is machine-independent for a given
# binary: two boxes and two tools (perf on Goldmont, cachegrind on a Xeon)
# put the same binary at 254 and 255.9. MHz is not portable and cannot be
# declared once.
#
# WHAT IS AND IS NOT AN INDEPENDENT CHECK -- stated here because presenting
# these as two agreeing witnesses would be the over-claim this file is
# supposed to prevent:
#
#   predicted MHz = (dIr/dt) / K_declared ,  observed MHz = dcycles/dt
#
# so predicted/observed is EXACTLY K_measured/K_declared. The MHz line is
# the K line restated in wall units, not a second measurement agreeing with
# the first. It is printed because MHz is the unit the goal is stated in,
# and it is labelled as derived.
#
# The genuinely separate quantity is the HARDWARE CEILING: measured IPC and
# effective clock on this box say how many instructions per second are
# available, and that is not a guess about the hardware -- it is read off
# it. Ceiling MHz = (clock * IPC) / K_floor.
#
# TWO-POINT MEASUREMENT. A single run folds in fixed startup (parse, ROM
# load, table init), which at a 200k window is most of the run -- that is
# why K reads 255.9 at 200k and 219.4 at 2M for the same binary. Both
# windows are run and subtracted, so startup cancels instead of being
# amortized away slowly and quoted as progress.
#
# Exit codes are distinguished on purpose (section 19): 0 = every declared
# row within tolerance, 1 = a VERDICT failure (a row is out), 2 = the
# INSTRUMENT could not run. A gate that exits nonzero for a reason other
# than its own is not a passing or failing gate, it is an unread one.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

# ---------------------------------------------------------------- selftest
# A checker that has never failed has not been shown to work. Each plant
# names the ONE check that must go red, and is rejected if the run reds for
# any other reason (section 41) -- a nonzero exit earned elsewhere is not
# this row passing. Exit codes are part of every case: 2 (instrument) and
# 1 (verdict) are different answers and a plant that confuses them is a
# plant that proves nothing.
#
# The negative control is not optional. A control with only the red half is
# satisfied by a checker that always fails.
if [ "${1:-}" = "--selftest" ]; then
    SD=$(mktemp -d); trap 'rm -rf "$SD"' EXIT
    DMG_SELF="${DMG_DIR:-$HERE/../../DMG}"
    pass=0; total=0
    # Real measurement is expensive, so the plants that only exercise the
    # COMPARISON run against a recorded point via K_FAKE_POINTS, which
    # injects the two measured tuples instead of running DMG. The plants
    # that exercise the MEASUREMENT still run it (K_SELFTEST_SLOW=1).
    base_budget() { printf 'WINDOW 2000000\nBASELINE 0\nK_TOTAL %s 8 declared\n' "$1"; }
    FAKE='120000000 60000000 0.100000 0;560000000 290000000 2.000000 2000000'
    chk() {  # $1 name, $2 expect-rc, $3 must-contain, then env assignments
        local name="$2" want="$3" pat="$4"; shift 4
        total=$((total+1))
        local out rc
        # The default points come FIRST so a plant's own K_FAKE_POINTS can
        # override them; with the default last, `env` let it win and three
        # plants silently measured the default and "passed".
        out=$(env K_FAKE_POINTS="$FAKE" "$@" bash "$HERE/k_oracle.sh" 2>&1); rc=$?
        if [ "$rc" != "$want" ]; then
            printf '  MISS %-46s rc=%s expected %s\n' "$name" "$rc" "$want"
            printf '%s\n' "$out" | tail -4 | sed 's/^/        /'; return
        fi
        case "$out" in *"$pat"*) printf '  ok   %-46s (rc=%s, its own reason)\n' "$name" "$rc"; pass=$((pass+1));;
            *) printf '  MISS %-46s rc=%s but WRONG reason\n' "$name" "$rc"
               printf '%s\n' "$out" | tail -4 | sed 's/^/        /';; esac
    }
    echo "== k_oracle selftest =="
    # The injected point is K = (560e6-120e6)/2e6 = 220.
    base_budget 219.4 > "$SD/ok.txt"
    chk x "P0 NEGATIVE CONTROL: a correct declaration passes" 0 "within 8%" K_BUDGET="$SD/ok.txt"
    base_budget 100 > "$SD/low.txt"
    chk x "P1 a declared row that is wrong goes red" 1 "OUTSIDE 8%"      K_BUDGET="$SD/low.txt"
    base_budget 400 > "$SD/high.txt"
    chk x "P2 red in the OTHER direction too"    1 "OUTSIDE 8%"          K_BUDGET="$SD/high.txt"
    # Both halves of the vacuity guard, separately (section 121): a budget
    # that parsed to nothing, and a budget that parsed fine but whose rows
    # were none of them measured. They are different bugs and a single
    # plant would leave one of them unproven.
    printf 'WINDOW 2000000\nBASELINE 0\n' > "$SD/norows.txt"
    chk x "P3a no rows at all REFUSES"          2 "parsed to ZERO rows"  K_BUDGET="$SD/norows.txt"
    printf 'WINDOW 2000000\nBASELINE 0\nAOT_IC 111.1 12 declared\n' > "$SD/nototal.txt"
    chk x "P3b rows present but NONE measured REFUSES" 2 "measured NONE" K_BUDGET="$SD/nototal.txt"
    printf '# only comments\n' > "$SD/empty.txt"
    chk x "P4 an empty budget REFUSES"           2 "no WINDOW/BASELINE"  K_BUDGET="$SD/empty.txt"
    chk x "P5 a missing budget REFUSES"          2 "nothing to diff"     K_BUDGET="$SD/nope.txt"
    # P6 pins the bug this oracle shipped its first green with: an
    # unparsable wall clock zeroed every derived figure, printed -nan%% as a
    # percent-of-ceiling, and exited 0.
    chk x "P6 a zero wall clock REFUSES, never passes" 2 "did not compute" \
        K_BUDGET="$SD/ok.txt" K_FAKE_POINTS='120000000 60000000 0 0;560000000 290000000 0 2000000'
    chk x "P7 two points that do not separate REFUSE" 2 "did not separate" \
        K_BUDGET="$SD/ok.txt" K_FAKE_POINTS='120000000 60000000 0.1 2000000;560000000 290000000 2.0 2000000'
    # P8: the whole point of the two-point subtraction. A run whose loop
    # exited EARLY must be divided by the cycles it actually ran, not the
    # cycles requested -- otherwise a short run reads as a low K, i.e. as
    # an improvement. Here the window point really executed 1M, so K is
    # 440, not 220, and the declared 219.4 must go red.
    chk x "P8 an early loop exit reds (actual, not requested, cycles)" 1 "OUTSIDE 8%" \
        K_BUDGET="$SD/ok.txt" K_FAKE_POINTS='120000000 60000000 0.1 0;560000000 290000000 2.0 1000000'
    # P9 is the only plant that proves the MEASUREMENT responds to a real
    # binary rather than to a number in a file. Everything above injects
    # points, so all of it would still pass if `point()` were stubbed out
    # -- a suite that tests only its own comparison logic (section 148: a
    # dead program satisfies a null control). Pointing the oracle at the
    # VM, whose K is an order of magnitude higher, is cheap and decisive.
    if [ "${K_SELFTEST_SLOW:-0}" = 1 ]; then
        VMB="${EIGS:-$HERE/../../EigenScript/src/eigenscript}"
        if [ -x "$VMB" ] && [ -f "$DMG_SELF/dmg.eigs" ]; then
            printf '#!/bin/bash\nexec %s %s "$@"\n' "$VMB" "$DMG_SELF/dmg.eigs" > "$SD/vm.sh"
            chmod +x "$SD/vm.sh"
            total=$((total+1))
            out=$(K_BUDGET="$SD/ok.txt" K_BIN="$SD/vm.sh" bash "$HERE/k_oracle.sh" 2>&1); rc=$?
            if [ "$rc" = 1 ] && [ "${out#*OUTSIDE}" != "$out" ]; then
                printf '  ok   %-46s (rc=1, %s)\n' "P9 REAL binary: the VM reds against the AOT budget" \
                       "$(printf '%s' "$out" | grep -oE 'measured +[0-9.]+' | head -1)"
                pass=$((pass+1))
            else
                printf '  MISS %-46s rc=%s\n' "P9 REAL binary: the VM reds against the AOT budget" "$rc"
                printf '%s\n' "$out" | tail -4 | sed 's/^/        /'
            fi
        else
            printf '  SKIP %-46s (no VM at %s)\n' "P9 REAL binary plant" "$VMB"
        fi
    else
        printf '  ---- P9 (real-binary plant) not run; set K_SELFTEST_SLOW=1.\n'
        printf '       Without it this suite proves the COMPARISON only, not the measurement.\n'
    fi
    # PIN THE POPULATION. "all plants passed" is satisfied by zero plants,
    # and a suite that quietly stops running a case looks exactly like a
    # suite whose cases all pass (section 121). The count is expected to
    # move when a plant is added -- editing this number is the deliberate
    # act that makes the addition reviewable.
    want=10; [ "${K_SELFTEST_SLOW:-0}" = 1 ] && want=12
    # P10 covers --origins, which K_FAKE_POINTS does not reach at all: that
    # mode takes its own measurement via callgrind, so every fast plant
    # above would still pass with the whole origins path broken -- and it
    # WAS broken, silently, in exactly that way: a mangled awk format
    # string made every row read OUT, and before that the mode fell all the
    # way through into the other one and printed PASS.
    if [ "${K_SELFTEST_SLOW:-0}" = 1 ]; then
        if [ -x "${K_BIN:-/nonexistent}" ] || [ -f "$DMG_SELF/dmg.eigs" ]; then
            printf 'WINDOW 2000000\nBASELINE 0\nK_TOTAL 219.4 8 d\nEIG 999 5 d\nAOT_IC 111.1 12 d\nOTHER 22.1 20 d\n' > "$SD/orig.txt"
            total=$((total+1))
            out=$(K_BUDGET="$SD/orig.txt" bash "$HERE/k_oracle.sh" --origins 2>&1); rc=$?
            if [ "$rc" = 1 ] && [ "${out#*EIG*OUTSIDE}" != "$out" ]; then
                printf '  ok   %-46s (rc=1, and only that row)\n' "P10 --origins reds the row that is wrong"
                pass=$((pass+1))
            else
                printf '  MISS %-46s rc=%s\n' "P10 --origins reds the row that is wrong" "$rc"
                printf '%s\n' "$out" | tail -6 | sed 's/^/        /'
            fi
        else
            printf '  SKIP %-46s (no DMG)\n' "P10 --origins plant"
        fi
    fi
    echo "== selftest $total run, $pass passed, $((total-pass)) failed =="
    if [ "$total" != "$want" ]; then
        echo "k_oracle: selftest ran $total plant(s), expected $want -- a case stopped running, which reads identically to a case that passed" >&2
        exit 2
    fi
    [ "$pass" = "$total" ]; exit $?
fi
DMG="${DMG_DIR:-$HERE/../../DMG}"
BUDGET="${K_BUDGET:-$HERE/k_budget.txt}"
ROM="${K_ROM:-$DMG/roms/cpu_instrs.gb}"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

refuse() { echo "k_oracle: REFUSE: $*" >&2; exit 2; }
# The preconditions for MEASURING are demanded only when measuring. With
# injected points nothing is executed, so requiring perf, a DMG checkout
# and a ROM would make the plants unrunnable anywhere those are absent --
# which is to say, in CI, which is the one place the plants most need to
# run. (An oracle nobody dispatches is not a gate; the plants are the half
# of this file that CAN be dispatched everywhere, and they are what prove
# it is still able to go red.)
if [ -z "${K_FAKE_POINTS:-}" ]; then
    command -v perf >/dev/null 2>&1 || refuse "no perf(1); K needs retired-instruction counts, and there is no substitute that is not a guess"
    perf stat -e instructions -x, /bin/true >/dev/null 2>&1 || refuse "perf cannot count instructions here (perf_event_paranoid=$(cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null))"
    [ -f "$DMG/dmg.eigs" ] || refuse "no DMG checkout at $DMG (set DMG_DIR)"
    [ -f "$ROM" ]      || refuse "no ROM at $ROM (set K_ROM)"
fi
[ -f "$BUDGET" ]   || refuse "no declared budget at $BUDGET -- there is nothing to diff against, and measuring K with nothing to compare it to is a number, not an oracle"

WINDOW=$(awk '$1=="WINDOW"{print $2}' "$BUDGET")
BASEW=$(awk '$1=="BASELINE"{print $2}' "$BUDGET")
[ -n "${WINDOW:-}" ] && [ -n "${BASEW:-}" ] || refuse "budget declares no WINDOW/BASELINE; K without its window is meaningless"

# With injected points nothing is executed, so building DMG here would cost
# a full compile per plant and measure nothing.
BIN="${K_BIN:-}"
if [ -n "${K_FAKE_POINTS:-}" ]; then
    BIN="${BIN:-/nonexistent-not-run}"
elif [ -z "$BIN" ]; then
    BIN="$WORK/dmg"
    echo "k_oracle: building DMG through the AOT ..." >&2
    bash "$HERE/build.sh" "$DMG/dmg.eigs" "$BIN" >"$WORK/build.log" 2>&1 ||
        { sed 's/^/    /' "$WORK/build.log" >&2; refuse "DMG did not build; the oracle measures a binary, so there is nothing to measure"; }
fi
[ -n "${K_FAKE_POINTS:-}" ] || [ -x "$BIN" ] || refuse "no runnable DMG binary at $BIN"

# One measured point. Emits: insn cycles wall emulated_cycles
point() {
    # SEPARATE statements on purpose: bash expands every word on a `local`
    # line BEFORE any of its assignments take effect, so `local tag="$2"
    # o="$WORK/$tag.out"` reads $tag while it is still unbound -- which under
    # `set -u` aborts the function and surfaced here as "the baseline point
    # did not run", i.e. an instrument bug wearing a measurement's clothes.
    local n="$1" tag="$2"
    local o="$WORK/$tag.out" p="$WORK/$tag.perf"
    # WALL TIME IS TAKEN HERE, NOT FROM PERF. `perf stat -x` omits the
    # "seconds time elapsed" line entirely -- the machine-readable format
    # drops it -- so parsing it out of -x, output yields empty forever. That
    # emptied every wall-derived figure to 0.00 and the oracle still exited
    # 0, printing a -nan%% "percent of ceiling" as though it were a result.
    # A derived value that cannot be computed is now a REFUSAL (below), not
    # a zero: this file exists to stop exactly that shape.
    local t0 t1
    t0=$(date +%s.%N)
    ( cd "$DMG" && perf stat -e instructions,cycles -x, -o "$p" \
        timeout 1800 "$BIN" "$ROM" --cycles "$n" ) >"$o" 2>"$WORK/$tag.err"
    local rc=$?
    t1=$(date +%s.%N)
    [ $rc -eq 0 ] || { echo "k_oracle: run at $n cycles exited $rc" >&2; sed 's/^/    /' "$WORK/$tag.err" >&2; return 1; }
    local insn cyc wall emu
    insn=$(awk -F, '$3=="instructions"{print $1}' "$p")
    cyc=$(awk -F, '$3=="cycles"{print $1}'        "$p")
    wall=$(awk -v a="$t0" -v b="$t1" 'BEGIN{printf "%.6f", b-a}')
    # The EMULATED cycle count comes from DMG's own report, never from the
    # --cycles request: the loop can exit early (HALT with no pending
    # interrupt, STOP, a cap hit), and dividing by the REQUESTED count would
    # then silently understate K by exactly the shortfall -- a wrong number
    # that looks like an improvement.
    emu=$(grep -oE '^Cycles: [0-9]+' "$o" | head -1 | awk '{print $2}')
    [ -n "$insn" ] && [ -n "$cyc" ] && [ -n "$emu" ] || {
        echo "k_oracle: could not parse a point at $n (insn='$insn' cyc='$cyc' emu='$emu')" >&2; return 1; }
    echo "$insn $cyc $wall $emu"
}

# ---------------------------------------------------------------- origins
# Per-origin attribution, exact rather than sampled. callgrind counts every
# instruction and attributes it to a symbol, so the buckets are arithmetic,
# not statistics -- which matters because the rows differ by less than
# sampling noise would. It costs ~30x wall (a 2M window is ~60s a point),
# which is why it is a separate mode and not part of every run.
#
# The SAME two-point subtraction applies per bucket. At a 100k window the
# libc allocator alone is 20% of instructions, nearly all of it startup;
# attributing that to emulation would be a straightforward lie.
if [ "${1:-}" = "--origins" ]; then
    command -v valgrind >/dev/null 2>&1 || refuse "no valgrind; --origins needs exact per-symbol counts"
    command -v callgrind_annotate >/dev/null 2>&1 || refuse "no callgrind_annotate"
    [ -x "$BIN" ] || refuse "no runnable DMG binary at $BIN"
    cg_point() {  # $1 cycles, $2 tag -> writes "$WORK/$2.bucket" as: BUCKET Ir
        local n="$1" tag="$2"
        ( cd "$DMG" && valgrind --tool=callgrind --callgrind-out-file="$WORK/$tag.cg"             "$BIN" "$ROM" --cycles "$n" ) >"$WORK/$tag.out" 2>"$WORK/$tag.vg" || return 1
        local emu
        emu=$(grep -oE '^Cycles: [0-9]+' "$WORK/$tag.out" | head -1 | awk '{print $2}')
        [ -n "$emu" ] || { echo "k_oracle: no cycle count from the $tag run" >&2; return 1; }
        # Bucket by symbol. The buckets are exhaustive by construction --
        # everything that is not eig_* or aot_* is OTHER -- and the sum is
        # asserted against the reported total below, so a parse that drops
        # lines cannot quietly shrink a bucket (a conserved quantity).
        callgrind_annotate --threshold=100 "$WORK/$tag.cg" 2>/dev/null |
          awk -v out="$WORK/$tag.bucket" -v emuf="$WORK/$tag.emu" -v emu="$emu" '
            /PROGRAM TOTALS/ { gsub(/,/,"",$1); total=$1; next }
            /:/ {
              line=$0
              if (match(line, /^[ ]*[0-9,]+/)) {
                v=substr(line, RSTART, RLENGTH); gsub(/[ ,]/,"",v)
                if (v=="") next
                fn=line; sub(/^[^)]*\)[ ]*/, "", fn); sub(/^.*:/, "", fn); sub(/ \[.*$/, "", fn)
                if (fn ~ /^eig_/)      eig += v
                else if (fn ~ /^aot_/) aot += v
                else                   oth += v
              }
            }
            END {
              printf "EIG %d\nAOT_IC %d\nOTHER %d\nTOTAL %d\n", eig, aot, oth, total > out
              printf "%d\n", emu > emuf
            }'
        # A helper that returns 0 having produced nothing is how --origins
        # first "ran": its awk died on a malformed format string, cg_point
        # returned success anyway, and with no `set -e` the caller carried on
        # and fell through into the OTHER mode, which then printed PASS. A
        # step now proves it produced what it claims to have produced.
        [ -s "$WORK/$tag.emu" ] || { echo "k_oracle: $tag produced no cycle count" >&2; return 1; }
        local nb; nb=$(awk 'END{print NR}' "$WORK/$tag.bucket" 2>/dev/null)
        [ "$nb" = 4 ] || { echo "k_oracle: $tag attribution produced ${nb:-0} bucket row(s), expected 4" >&2; return 1; }
        return 0
    }
    echo "k_oracle: --origins, exact attribution via callgrind (slow; ~30x)" >&2
    cg_point "$BASEW"  cgbase || refuse "the callgrind baseline point did not run"
    cg_point "$WINDOW" cgwin  || refuse "the callgrind window point did not run"
    g() { awk -v k="$2" '$1==k{print $2}' "$WORK/$1.bucket"; }
    de=$(( $(cat "$WORK/cgwin.emu") - $(cat "$WORK/cgbase.emu") ))
    [ "$de" -gt 0 ] || refuse "the two callgrind points did not separate in emulated cycles"
    # Conservation: the buckets must add up to the total callgrind reported.
    # A bucket that silently lost lines would otherwise read as an origin
    # that got cheaper.
    for t in cgbase cgwin; do
        sum=$(( $(g $t EIG) + $(g $t AOT_IC) + $(g $t OTHER) ))
        tot=$(g $t TOTAL)
        awk -v a="$sum" -v b="$tot" 'BEGIN{exit !(b>0 && (a-b<b*0.01 && b-a<b*0.01))}' ||
            refuse "$t buckets sum to $sum but callgrind reported $tot -- the attribution dropped instructions, so a bucket would read low"
    done
    printf '
== per-origin, %d emulated cycles net of startup (exact) ==
' "$de"
    ofails=0; oexam=0; orows=0
    while read -r name declared tol _rest; do
        case "$name" in ''|\#*|WINDOW|BASELINE|K_TOTAL) continue;; esac
        orows=$((orows+1))
        d=$(( $(g cgwin "$name") - $(g cgbase "$name") ))
        oexam=$((oexam+1))
        read -r v dev <<<"$(awk -v m="$d" -v e="$de" -v dc="$declared" -v t="$tol" 'BEGIN{
            k=m/e; p=(k-dc)/dc*100; printf "%s %.1f\n", ((p<0?-p:p)<=t?"ok":"OUT"), p }')"
        k=$(awk -v m="$d" -v e="$de" 'BEGIN{printf "%.1f", m/e}')
        if [ "$v" = ok ]; then
            printf '  %-10s declared %8.1f  measured %8.1f  %+6.1f%% within %s%%
' "$name" "$declared" "$k" "$dev" "$tol"
        else
            printf '  %-10s declared %8.1f  measured %8.1f  %+6.1f%% OUTSIDE %s%%
' "$name" "$declared" "$k" "$dev" "$tol"
            ofails=$((ofails+1))
        fi
    done < "$BUDGET"
    [ "$orows" -gt 0 ] || refuse "no per-origin rows in the budget"
    [ "$oexam" -gt 0 ] || refuse "parsed $orows origin row(s) and measured NONE"
    tk=$(awk -v a="$(g cgwin TOTAL)" -v b="$(g cgbase TOTAL)" -v e="$de" 'BEGIN{printf "%.1f", (a-b)/e}')
    printf '  %-10s %27s %8.1f  (callgrind total, for cross-check against perf)
' "K_TOTAL" "" "$tk"
    # Per-symbol, WINDOW MINUS BASELINE. Reading the window file alone and
    # calling the result "net of startup" would be false labelling, and at a
    # small window it is badly false: the libc allocator is 20% of a 100k
    # run and almost none of that is emulation.
    sym() { callgrind_annotate --threshold=100 "$WORK/$1.cg" 2>/dev/null |
        awk '/:/ && match($0,/^[ ]*[0-9,]+/){
               v=substr($0,RSTART,RLENGTH); gsub(/[ ,]/,"",v)
               fn=$0; sub(/^.*:/,"",fn); sub(/ \[.*$/,"",fn); gsub(/^ +| +$/,"",fn)
               if (fn!="" && v!="") printf "%s\t%s\n", fn, v }'; }
    printf '\n== the ten largest single symbols, net of startup, Ir per emulated cycle ==\n'
    TAB=$(printf '\t')
    join -t "$TAB" -a1 -e 0 -o 0,1.2,2.2 \
         <(sym cgwin  | sort -t "$TAB" -k1,1) \
         <(sym cgbase | sort -t "$TAB" -k1,1) 2>/dev/null |
      awk -F'\t' '{d=$2-$3; if(d>0) printf "%d\t%s\n", d, $1}' |
      sort -rn | head -10 |
      awk -F'\t' -v e="$de" '{printf "  %8.2f  %s\n", $1/e, $2}' 
    if [ "$ofails" -gt 0 ]; then
        printf '
k_oracle: FAIL -- %d origin row(s) outside tolerance.
' "$ofails"; exit 1
    fi
    printf '
k_oracle: PASS -- %d of %d origin row(s) within tolerance.
' "$oexam" "$orows"
    exit 0
fi

# K_FAKE_POINTS injects two recorded tuples in place of running DMG. It
# exists for --selftest and nothing else: the comparison logic has to be
# testable without a 2M-cycle run per plant. It is deliberately NOT a way
# to supply measurements -- the format is undocumented outside this file
# and every plant that uses it is checking the comparison, never a result.
if [ -n "${K_FAKE_POINTS:-}" ]; then
    lo=${K_FAKE_POINTS%%;*}; hi=${K_FAKE_POINTS##*;}
else
    # REPEAT AND TAKE MEDIANS. The counter-derived figure (K) is stable to
    # 0.2% across runs because retired instructions barely vary. The WALL
    # figures are not: dw is a small difference of two larger noisy numbers
    # (startup is ~0.4s, the 2M-cycle emulation is ~0.1s), so ordinary
    # scheduling noise in the baseline propagates straight into clock and
    # MHz. Measured: a single pair reported 2.172 GHz on one run and 4.501
    # GHz on the next, on a box whose clock is ~2.1 -- i.e. the derived MHz
    # was wrong by 2x while K moved 0.1%. Medians of REPS pairs, not one.
    REPS="${K_REPS:-3}"
    echo "k_oracle: two-point measurement, window=$WINDOW baseline=$BASEW, $REPS reps" >&2
    : > "$WORK/pairs"
    for r in $(seq 1 "$REPS"); do
        l=$(point "$BASEW"  "base$r") || refuse "baseline point $r did not run"
        h=$(point "$WINDOW" "win$r")  || refuse "window point $r did not run"
        echo "$l $h" >> "$WORK/pairs"
    done
    med() { sort -g | awk '{v[NR]=$1} END{ if(NR==0) exit 1; print (NR%2)? v[(NR+1)/2] : (v[NR/2]+v[NR/2+1])/2 }'; }
    # Median each DERIVED quantity, not each raw count: the ratio of medians
    # is not the median of ratios, and it is the ratios that are compared.
    i0=0; c0=0; w0=0; e0=0
    i1=$(awk '{print $5-$1}' "$WORK/pairs" | med)
    c1=$(awk '{print $6-$2}' "$WORK/pairs" | med)
    w1=$(awk '{print $7-$3}' "$WORK/pairs" | med)
    e1=$(awk '{print $8-$4}' "$WORK/pairs" | med)
    [ -n "$i1" ] && [ -n "$e1" ] || refuse "no usable pairs after $REPS rep(s)"
    lo="0 0 0 0"; hi="$i1 $c1 $w1 $e1"
    spread=$(awk '{print ($5-$1)/($8-$4)}' "$WORK/pairs" |
             sort -g | awk 'NR==1{a=$1} END{ if(a>0) printf "%.2f", $1/a; else print "0" }')
    echo "k_oracle: K spread across reps (max/min) = ${spread}x" >&2
fi
read -r i0 c0 w0 e0 <<<"$lo"
read -r i1 c1 w1 e1 <<<"$hi"

read -r K IPC GHZ MHZ DEMU DIR <<<"$(awk -v i0="$i0" -v c0="$c0" -v w0="$w0" -v e0="$e0" \
                                        -v i1="$i1" -v c1="$c1" -v w1="$w1" -v e1="$e1" 'BEGIN{
    di=i1-i0; dc=c1-c0; dw=w1-w0; de=e1-e0;
    if (de<=0 || di<=0) { print "ERR 0 0 0 0 0"; exit }
    printf "%.4f %.4f %.4f %.4f %d %d\n", di/de, (dc>0? di/dc : 0), (dw>0? dc/dw/1e9 : 0), (dw>0? de/dw/1e6 : 0), de, di;
}')"
[ "$K" = "ERR" ] && refuse "the two points did not separate (window <= baseline in emulated cycles or instructions); nothing can be subtracted"
# Every figure below is DERIVED, and a derived figure that did not compute
# must stop the run. Printing 0.00 and exiting 0 is how this oracle passed
# its own first green with an unparsable wall clock and a -nan%% ceiling.
for _n in K IPC GHZ MHZ; do
    _v=$(eval printf '%s' "\$$_n")
    case "$_v" in *nan*|*inf*|"") refuse "$_n did not compute ('$_v') -- refusing to report a derived number the run did not produce";; esac
    awk -v v="$_v" 'BEGIN{exit !(v>0)}' || refuse "$_n did not compute (got '$_v', not positive) -- refusing to report a derived number the run did not produce"
done

printf '\n== measured, %d emulated cycles net of startup ==\n' "$DEMU"
printf '  K            %8.1f  host instructions retired per emulated cycle\n' "$K"
printf '  IPC          %8.2f  measured, this binary on this box\n' "$IPC"
printf '  clock        %8.3f GHz effective under load\n' "$GHZ"
printf '  speed        %8.2f MHz emulated (real DMG is 4.19)\n' "$MHZ"

fails=0; examined=0; rows=0
printf '\n== declared vs measured ==\n'
while read -r name declared tol _rest; do
    case "$name" in ''|\#*) continue;; esac
    case "$name" in WINDOW|BASELINE) continue;; esac
    rows=$((rows+1))
    # Only K_TOTAL is measurable with `perf stat` alone; the per-origin rows
    # need symbol attribution (perf record), which is a separate tier. They
    # are reported as NOT MEASURED, never as passing -- a gate that renders
    # "never measured" and "measured, fine" identically is the failure mode
    # in mechanical-gates section 11, and these rows are exactly where it
    # would bite, because the interesting one (AOT_IC) is the tier 1 target.
    if [ "$name" != "K_TOTAL" ]; then
        printf '  %-10s declared %8.1f   NOT MEASURED (needs symbol attribution; see --origins)\n' "$name" "$declared"
        continue
    fi
    examined=$((examined+1))
    read -r verdict delta <<<"$(awk -v m="$K" -v d="$declared" -v t="$tol" 'BEGIN{
        dev = (m-d)/d*100; printf "%s %.1f\n", ((dev<0?-dev:dev)<=t ? "ok":"OUT"), dev }')"
    if [ "$verdict" = ok ]; then
        printf '  %-10s declared %8.1f  measured %8.1f  %+6.1f%% within %s%%\n' "$name" "$declared" "$K" "$delta" "$tol"
    else
        printf '  %-10s declared %8.1f  measured %8.1f  %+6.1f%% OUTSIDE %s%%\n' "$name" "$declared" "$K" "$delta" "$tol"
        fails=$((fails+1))
    fi
done < "$BUDGET"

# Vacuity, both halves (section 121): a budget that parsed to nothing, or a
# run that checked none of what it parsed, must not read as agreement.
[ "$rows" -gt 0 ] || refuse "the budget file parsed to ZERO rows -- the oracle would have printed a clean sheet having compared nothing"
[ "$examined" -gt 0 ] || refuse "parsed $rows declared row(s) and measured NONE of them"

K_DECL=$(awk '$1=="K_TOTAL"{print $2}' "$BUDGET")
printf '\n== the goal, in the unit the goal is stated in ==\n'
awk -v k="$K" -v kd="$K_DECL" -v mhz="$MHZ" -v ipc="$IPC" -v ghz="$GHZ" 'BEGIN{
    printf "  predicted   %8.2f MHz  from the DECLARED K (%.1f) and this box\n", ghz*1e3*ipc/kd, kd;
    printf "  observed    %8.2f MHz  DERIVED, not independent: predicted/observed == K_measured/K_declared\n", mhz;
    printf "\n  ceiling     %8.2f MHz  if K reached 86 (issue #233: all compiler scaffolding gone)\n", ghz*1e3*ipc/86;
    printf "  ceiling     %8.2f MHz  if K reached 40 (a tight C DMG core -- NOT reachable by the compiler alone)\n", ghz*1e3*ipc/40;
    printf "\n  at K=%.1f this binary is at %.1f%% of its K=40 ceiling on THIS hardware.\n", k, (ghz*1e3*ipc/k)/(ghz*1e3*ipc/40)*100;
}'

if [ "$fails" -gt 0 ]; then
    printf '\nk_oracle: FAIL -- %d declared row(s) outside tolerance.\n' "$fails"
    printf 'k_oracle: a row moving is not automatically bad. It means the declaration in\n'
    printf '          %s is now wrong, and the fix is to change that row\n' "$BUDGET"
    printf '          AND its basis line in the same commit as the code that earned it.\n'
    exit 1
fi
printf '\nk_oracle: PASS -- %d of %d declared row(s) measured, all within tolerance.\n' "$examined" "$rows"
exit 0
