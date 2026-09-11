#!/usr/bin/env bash
# Seven-tier gate for an ouroboros checkout and its pinned runtime worktree.
# Exactly the original seven tiers, sequentially; each tier owns its counts
# and internal checks. This driver adds checkout/pin/path and aggregate checks.
set -Eeuo pipefail

refuse() {
    printf 'GATE REFUSED: %s\n' "$*" >&2
    exit 2
}

if [[ ${1:-} == --help && $# == 1 ]]; then
    cat <<'USAGE'
Usage: AOT_REPO=/path/ouroboros EIGS_CO=/path/pinned-EigenScript \
       DMG_DIR=/path/DMG GATE_LOG=/tmp/gate.log bash aot/gate_tiers.sh

AOT_REPO and EIGS_CO are required (EIGS_DIR is accepted as an EIGS_CO alias).
DMG_DIR defaults to the DMG sibling of AOT_REPO. GATE_LOG is optional; without
it output stays on stdout/stderr. All runtime tier paths derive from EIGS_CO.

Exit 0: all seven tiers returned 0. Exit 1: at least one tier failed/missing.
Exit 2: setup refused before tiers. Only a completed seven-tier attempt emits
the final verdict and TIERS_DONE. A nonzero tier never stops the later tiers.
USAGE
    exit 0
fi
[[ $# == 0 ]] || refuse 'unexpected arguments (use --help)'

[[ -n ${AOT_REPO:-} ]] || refuse 'set AOT_REPO to the ouroboros checkout'
EIGS_CO=${EIGS_CO:-${EIGS_DIR:-}}
[[ -n $EIGS_CO ]] || refuse 'set EIGS_CO (or EIGS_DIR) to the pinned oracle checkout'
for tool in bash git awk realpath mkdir dirname; do
    command -v "$tool" >/dev/null || refuse "missing tool: $tool"
done
AOT_REPO=$(realpath -e -- "$AOT_REPO") || refuse 'AOT_REPO does not exist'
EIGS_CO=$(realpath -e -- "$EIGS_CO") || refuse 'EIGS_CO does not exist'
[[ -d $AOT_REPO/aot && -f $AOT_REPO/.devcontainer/Dockerfile ]] || refuse 'AOT_REPO lacks aot/ or .devcontainer/Dockerfile'
[[ -d $EIGS_CO ]] || refuse 'EIGS_CO must be a directory'
DMG_DIR=$(realpath -m -- "${DMG_DIR:-$AOT_REPO/../DMG}") || refuse 'cannot resolve DMG_DIR'

if [[ -n ${GATE_LOG:-} ]]; then
    GATE_LOG=$(realpath -m -- "$GATE_LOG") || refuse 'cannot resolve GATE_LOG'
    [[ $GATE_LOG != "$EIGS_CO" && $GATE_LOG != "$EIGS_CO/"* ]] || refuse 'GATE_LOG must be outside the oracle checkout'
    mkdir -p -- "$(dirname -- "$GATE_LOG")" || refuse 'cannot create gate log directory'
    exec >"$GATE_LOG" 2>&1
fi

oracle_root=$(git -C "$EIGS_CO" rev-parse --show-toplevel) || refuse 'oracle is not a Git checkout'
[[ $(realpath -e -- "$oracle_root") == "$EIGS_CO" ]] || refuse 'EIGS_CO must name the checkout root'
oracle_status=$(git -C "$EIGS_CO" status --porcelain=v1 --untracked-files=all) || refuse 'cannot inspect oracle cleanliness'
[[ -z $oracle_status ]] || refuse 'the oracle checkout has uncommitted changes (develop in a worktree)'

# A single literal Dockerfile pin is authoritative. No eval and no fetching:
# an unavailable tag/commit must be made available deliberately by the caller.
pin=$(awk '
    /^[[:space:]]*ARG[[:space:]]+EIGS_REF=/ {
        n++; value=$0
        sub(/^[[:space:]]*ARG[[:space:]]+EIGS_REF=/, "", value)
        sub(/[[:space:]]+#.*$/, "", value)
        sub(/[[:space:]]+$/, "", value)
    }
    END {if (n!=1 || value=="") exit 1; print value}
' "$AOT_REPO/.devcontainer/Dockerfile") || refuse 'Dockerfile must contain exactly one nonempty ARG EIGS_REF= pin'
case $pin in
    \"*\") pin=${pin:1:${#pin}-2} ;;
    \'*\') pin=${pin:1:${#pin}-2} ;;
esac
[[ $pin =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || refuse "Dockerfile EIGS_REF is not a literal Git ref: $pin"
pin_commit=$(git -C "$EIGS_CO" rev-parse --verify --end-of-options "${pin}^{commit}") || refuse "cannot resolve Dockerfile pin to a commit: $pin"
oracle_commit=$(git -C "$EIGS_CO" rev-parse --verify HEAD) || refuse 'cannot resolve oracle HEAD'
printf 'GATE ORACLE source=%s ref=%s resolved_commit=%s actual_commit=%s checkout=%s\n' \
    "$AOT_REPO/.devcontainer/Dockerfile" "$pin" "$pin_commit" "$oracle_commit" "$EIGS_CO"
[[ $oracle_commit == "$pin_commit" ]] || refuse "oracle at $oracle_commit, Dockerfile pin $pin resolves to $pin_commit"

export AOT_REPO EIGS_CO DMG_DIR
export EIGS_DIR="$EIGS_CO" EIGS_ROOT="$EIGS_CO" LIB_DIR="$EIGS_CO/lib" EIGS="$EIGS_CO/src/eigenscript"
[[ -x $EIGS && -f $EIGS ]] || refuse "oracle binary is not executable: $EIGS"
printf 'GATE PATHS AOT_REPO=%s EIGS=%s EIGS_DIR=%s EIGS_ROOT=%s LIB_DIR=%s DMG_DIR=%s\n' \
    "$AOT_REPO" "$EIGS" "$EIGS_DIR" "$EIGS_ROOT" "$LIB_DIR" "$DMG_DIR"
canary_baseline="$AOT_REPO/aot/test/canary/dmg_cpu_instrs.out"
[[ -f $canary_baseline && -r $canary_baseline && -s $canary_baseline ]] || \
    refuse "canary baseline must be a readable nonempty regular file: $canary_baseline"

run_tier() {
    local result_name=$1 heading=$2 directory=$3 script=$4 tier_rc
    printf '=== %s ===\n' "$heading"
    if [[ ! -f $directory/$script || ! -r $directory/$script || ! -s $directory/$script ]]; then
        printf 'GATE TIER MISSING/UNREADABLE/EMPTY: %s\n' "$directory/$script" >&2
        tier_rc=127
    elif (cd -- "$directory" && bash "$script"); then
        tier_rc=0
    else
        tier_rc=$?
    fi
    printf -v "$result_name" '%s' "$tier_rc"
}

run_tier k 'core check' "$AOT_REPO/aot" core_check.sh
run_tier a 'AOT suite' "$AOT_REPO/aot" test/run.sh
run_tier b 'self-host suite' "$AOT_REPO" test/run.sh
run_tier c 'DMG canary' "$AOT_REPO/aot" canary_dmg.sh
run_tier d 'stdlib sweep' "$AOT_REPO/aot" stdlib_sweep.sh
run_tier e 'corpus run-differential' "$AOT_REPO/aot" corpus_diff.sh
run_tier l 'leak tier' "$AOT_REPO/aot" test/leak.sh
printf '=== verdict aot=%s selfhost=%s canary=%s stdlib=%s corpus=%s leak=%s core=%s ===\n' "$a" "$b" "$c" "$d" "$e" "$l" "$k"
printf 'TIERS_DONE\n'
if ((a || b || c || d || e || l || k)); then
    exit 1
fi
exit 0
