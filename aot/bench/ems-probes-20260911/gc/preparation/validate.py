#!/usr/bin/env python3
"""Compare saved successful GC diagnostic logs. Does not execute programs."""
import argparse
import json
from pathlib import Path

def read(path):
    lines=path.read_text().splitlines()
    rows=[json.loads(s.removeprefix('GC_PROBE_COLLECTION ')) for s in lines if s.startswith('GC_PROBE_COLLECTION ')]
    totals=[json.loads(s.removeprefix('GC_PROBE_TOTAL ')) for s in lines if s.startswith('GC_PROBE_TOTAL ')]
    assert len(totals)==1,'need exactly one complete total record'
    t=totals[0]
    assert all(type(t[k]) is int and t[k]>=0 for k in t if k not in ['slots','edges'])
    assert t['calls']==t['skipped_busy']+t['skipped_empty']+t['started']
    assert t['started']==t['completed']+t['aborted']
    assert t['completed']==len(rows)>0 and t['aborted']==0
    assert [r['ordinal'] for r in rows]==list(range(1,len(rows)+1))
    for key in ['universe','reclaimed','survivors','seeds']:
        assert t[key]==sum(r[key] for r in rows),f'bad total {key}'
    assert t['universe']==t['reclaimed']+t['survivors']
    for key in ['slots','edges']:
        assert len(t[key])==4 and all(len(r)==3 for r in t[key])
        assert all(type(x) is int and x>=0 for r in t[key] for x in r)
    for p in range(4):
        assert all(e<=s for e,s in zip(t['edges'][p],t['slots'][p]))
    return rows,t

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('baseline_stderr',type=Path)
    ap.add_argument('candidate_stderr',type=Path)
    a=ap.parse_args()
    br,b=read(a.baseline_stderr); cr,c=read(a.candidate_stderr)
    assert br==cr,'collection-by-collection population/reclamation mismatch'
    for k in ['calls','skipped_busy','skipped_empty','started','completed','aborted','universe','reclaimed','survivors','seeds']:
        assert b[k]==c[k],f'total mismatch {k}'
    assert b['slots'][0]==b['slots'][1], 'baseline discovery/count slot mismatch'
    assert b['edges'][0]==b['edges'][1], 'baseline discovery/count edge mismatch'
    assert sum(b['slots'][1])>0,'baseline count phase not reached'
    assert b['nochild_skips']==0 and c['nochild_skips']>0,'no-child skip path not evidenced'
    assert c['slots'][1]==[0,0,0] and c['edges'][1]==[0,0,0],'count phase not removed'
    assert c['slots'][0]==b['slots'][0] and c['edges'][0]==b['edges'][0], 'discovery changed'
    assert c['edges'][2]==b['edges'][2],'mark accepted edges changed'
    assert c['slots'][3]==b['slots'][3], 'reclamation edge clearing changed'
    assert sum(c['slots'][2])<sum(b['slots'][2]),'no measured mark-slot reduction'
    print(json.dumps({'collections':c['completed'],'nodes':c['universe'],'reclaimed':c['reclaimed'],
                      'removed_count_slots':sum(b['slots'][1]),
                      'removed_mark_slots':sum(b['slots'][2])-sum(c['slots'][2]),
                      'nochild_skips':c['nochild_skips']}))
if __name__=='__main__': main()
