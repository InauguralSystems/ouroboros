#!/usr/bin/env python3
"""Freeze scalar input and replace only each len resolver's own dispatch token.
Source generation only: never invokes a compiler, VM, or workload.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

SOURCE_HASH = 'a2d149ba8997c665db5144f1a1c5e1874658e0195df2cbc0aafc257c53106df1'
HEADER_HASH = 'ab7c944b18fed52ec1dc0f9d280dab73c296051feb60a6db44bc2dce09d6c4a1'
EXPECTED = 211
TOKEN = re.compile(r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_][A-Za-z_0-9]*|[^\s]')

def sha(data):
    return hashlib.sha256(data).hexdigest()

def write_once(path, data):
    if path.exists():
        assert path.read_bytes() == data, f'refusing to overwrite differing snapshot: {path}'
    else:
        path.write_bytes(data)

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source', type=Path, default=Path('/tmp/ems-close-20260911/ems-scalar-production.c'))
    ap.add_argument('--header', type=Path, default=Path('/home/jon/src/wt/ouro-gap-20260911/aot/aot_rt.h'))
    ap.add_argument('--out', type=Path, default=Path(__file__).resolve().parent)
    a = ap.parse_args()
    raw, header = a.source.read_bytes(), a.header.read_bytes()
    assert sha(raw) == SOURCE_HASH, 'wrong scalar generated C'
    assert sha(header) == HEADER_HASH, 'wrong scalar runtime header'
    src = raw.decode()
    toks = [m for m in TOKEN.finditer(src) if not m[0].startswith(('/*', '//'))]
    words = [m[0] for m in toks]
    # Give each brace block a unique identity. A nested call expression has
    # a different owner even when it uses the same _sqcf/_sqca variable names.
    owners, stack = [], []
    for i, w in enumerate(words):
        if w == '{':
            stack.append(i)
        owners.append(stack[-1] if stack else None)
        if w == '}':
            assert stack, 'unbalanced close brace'
            stack.pop()
    assert not stack, 'unbalanced open brace'
    resolvers = [i for i, w in enumerate(words)
                 if w == 'aot_call_resolve' and words[i+1:i+5] == ['(', '__eigs_g', ',', '"len"']]
    assert len(resolvers) == EXPECTED, f'len resolver population {len(resolvers)} != {EXPECTED}'
    dispatch_by_owner = {}
    for j, w in enumerate(words):
        if w == 'aot_call_dispatch':
            dispatch_by_owner.setdefault(owners[j], []).append(j)
    edits, sites = [], []
    for i in resolvers:
        assert words[i-3:i] == ['*', '_sqcf', '='], 'unexpected callee storage'
        assert words[i+5:i+7] == [',', '&'] and words[i+8:i+10] == [')', ';']
        owner = owners[i]
        own_dispatches = dispatch_by_owner.get(owner, [])
        assert len(own_dispatches) == 1, f'{words[i+7]}: own dispatch count {len(own_dispatches)}'
        j = own_dispatches[0]
        assert j > i, 'dispatch precedes resolver'
        assert words[j+1:j+6] == ['(', '_sqcf', ',', '_sqca', ')']
        edits.append((toks[j].start(), toks[j].end()))
        sites.append({'ic': words[i+7], 'resolver_line': src.count('\n', 0, toks[i].start())+1,
                      'dispatch_line': src.count('\n', 0, toks[j].start())+1,
                      'dispatch_byte_offset': toks[j].start(), 'block_token': owner})
    assert len(set(edits)) == EXPECTED, 'duplicate replacement'
    result = src
    for lo, hi in sorted(edits, reverse=True):
        assert result[lo:hi] == 'aot_call_dispatch'
        result = result[:lo] + 'len_probe_dispatch' + result[hi:]
    assert src.count('#include "aot_rt.h"') == 1
    result = result.replace('#include "aot_rt.h"', '#include "aot_rt.h"\n#include "len_probe.h"', 1)
    assert result.count('len_probe_dispatch(') == EXPECTED
    assert src.count('aot_call_dispatch(') - result.count('aot_call_dispatch(') == EXPECTED
    # Strong byte-coverage check: reversal must reproduce all original bytes.
    restored = result.replace('len_probe_dispatch(', 'aot_call_dispatch(').replace('\n#include "len_probe.h"', '', 1)
    assert restored == src, 'unaccounted source mutation'
    a.out.mkdir(parents=True, exist_ok=True)
    helper = Path(__file__).with_name('len_probe.h').read_bytes()
    artifacts = {'baseline.c': raw, 'aot_rt.h': header, 'len_probe.h': helper,
                 'candidate.c': result.encode()}
    for name, data in artifacts.items():
        write_once(a.out/name, data)
    manifest = {'label': 'timing-only; no correctness certification',
                'source': str(a.source.resolve()), 'header': str(a.header.resolve()),
                'scalar_commit': '7d6b6fa7f3dbbfb1228abcb1f437db564d9d2283',
                'source_sha256': SOURCE_HASH, 'header_sha256': HEADER_HASH,
                'expected_len_resolvers': EXPECTED, 'changed_dispatches': len(edits),
                'original_all_dispatches': src.count('aot_call_dispatch('),
                'remaining_all_dispatches': result.count('aot_call_dispatch('),
                'reversible_source_bytes': True, 'sites': sites,
                'sha256': {name: sha(data) for name, data in artifacts.items()},
                'generator_sha256': sha(Path(__file__).read_bytes())}
    write_once(a.out/'manifest.json', (json.dumps(manifest, indent=2)+'\n').encode())
    print(json.dumps({k: manifest[k] for k in ['changed_dispatches', 'original_all_dispatches', 'remaining_all_dispatches', 'reversible_source_bytes']}))

if __name__ == '__main__':
    main()
