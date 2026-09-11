#!/usr/bin/env python3
"""Validate a saved successful diagnostic run; no builds or program execution."""
import argparse
import json
from pathlib import Path
ap = argparse.ArgumentParser()
ap.add_argument('stderr', type=Path)
ap.add_argument('--control', action='store_true')
a = ap.parse_args()
lines = [s for s in a.stderr.read_text().splitlines() if s.startswith('LEN_PROBE ')]
assert len(lines) == 1, f'expected one counter record, got {len(lines)}'
c = json.loads(lines[0][len('LEN_PROBE '):])
assert set(c) == {'entry', 'eligible', 'fallback'}
assert all(type(v) is int and v >= 0 for v in c.values())
assert c['entry'] == c['eligible'] + c['fallback'], 'counter partition failure'
assert c['entry'] > 0 and c['eligible'] > 0, 'zero reach is not a negative timing result'
if a.control:
    assert c == {'entry': 4, 'eligible': 2, 'fallback': 2}, f'control mismatch: {c}'
print(json.dumps(c, sort_keys=True))
