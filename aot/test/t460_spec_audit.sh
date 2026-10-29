#!/bin/bash
# Current trace tapes put a stream id in every L/A record. Prove the audit
# finds the planted name, and that an interleaved stream cannot donate its line.
set -u
cd "$(dirname "$0")/.."
EIG="${EIGS:-../../EigenScript/src/eigenscript}"

check() {
    tool="$1"
    out=$("$EIG" "$tool" test/t460_spec_audit.tape test/t460_spec_audit.dump 2>&1) || return 1
    printf '%s\n' "$out" | grep -q '^CONFIRMED module planted kind=double line=7 assigns=1$' || return 1
    printf '%s\n' "$out" | grep -q '^# tape: .* (1 A-records, 1 names)$' || return 1
    ! printf '%s\n' "$out" | grep -q '^NOTAPE module planted '
}

if check tools/spec_audit.eigs; then
    echo 'PASS: spec_audit reads v5+ stream-qualified A/L records'
else
    echo 'FAIL: spec_audit did not find the planted current-format name on its own stream line'
    exit 1
fi

# Plant the old parser back into a private copy. This is a product of the
# gate, never an input to a real run; the clean control above passed first.
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
python3 - tools/spec_audit.eigs "$tmp/spec_audit.eigs" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1]).read_text()
old = '''            asp is index_of of [arest, " "]
            if asp > 0:
                astream is substr of [arest, 0, asp]
                apayload is substr of [arest, asp + 1, (len of arest) - asp - 1]
                eqp is index_of of [apayload, "="]
            else:
                eqp is 0 - 1'''
new = '''            astream is "0"
            apayload is arest
            eqp is index_of of [apayload, "="]'''
if p.count(old) != 1:
    raise SystemExit('fault anchor did not hit exactly one parser site')
Path(sys.argv[2]).write_text(p.replace(old, new))
PY
if check "$tmp/spec_audit.eigs"; then
    echo 'FAIL: spec_audit gate accepted the planted pre-v5 parser'
    exit 1
else
    echo 'PASS: spec_audit gate rejects planted pre-v5 parser'
fi
