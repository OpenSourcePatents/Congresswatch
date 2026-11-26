"""Prove the focused correction preserves raw inputs and non-SEC scoring."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sec_identity import eligible_sec_signals
from validation.scrub_sec_outputs import scrub_record


def main():
    base=json.loads((ROOT/'validation/sec_wrong_owners.json').read_text())['base_sha']
    def original(path):
        return json.loads(subprocess.check_output(['git','show',f'{base}:{path}'],cwd=ROOT))
    current=json.loads((ROOT/'data/members.json').read_text())
    old={m['id']:m for m in original('data/members.json')}
    allowed={'score','score_components','flags','sec_attribution_version'}
    score_changes=[];raw_total=0
    for m in current:
        bid=m['id'];path=f'data/details/{bid}.json'
        before=original(path);after=json.loads((ROOT/path).read_text())
        for label,a,b in [('roster',old[bid],m),('detail',before,after)]:
            changed={k for k in a.keys()|b.keys() if a.get(k)!=b.get(k)}
            assert changed<=allowed,(bid,label,changed-allowed)
            assert [x for x in a.get('flags',[]) if x!='trade']==[x for x in b['flags'] if x!='trade']
        for key,value in before['score_components'].items():
            if key!='trade_timing':assert after['score_components'][key]==value,(bid,key)
        assert m['score']==after['score']==min(sum(after['score_components'].values()),100)
        assert m['score_components']==after['score_components']
        assert scrub_record(after,after)==after,bid
        assert scrub_record(m,after)==m,bid
        assert eligible_sec_signals(m)==eligible_sec_signals(after)==0,bid
        assert 'trade' not in m['flags'] and 'trade' not in after['flags']
        raw_total+=after.get('corporate_insider_signals',0) or 0
        if before['score']!=after['score']:
            score_changes.append({'id':bid,'name':m['name'],'before':before['score'],'after':after['score'],
                                  'ptr_points_preserved':after['score_components']['trade_timing']})
    stats=json.loads((ROOT/'data/stats.json').read_text())
    assert stats['total_insider_signals']==0
    assert stats['members_with_scores']==sum(m['score']>0 for m in current)
    assert stats['high_anomaly']==sum(m['score']>=60 for m in current)
    beforestats=original('data/stats.json')
    assert {k for k in beforestats.keys()|stats.keys() if beforestats.get(k)!=stats.get(k)} <= {
        'total_members','members_with_scores','high_anomaly','total_insider_signals'}
    # Check every data path, including corpus aggregates outside the member vault.
    paths=subprocess.check_output(['git','diff',base,'--name-only','--','data'],cwd=ROOT,text=True).splitlines()
    assert set(paths)<={f'data/details/{bid}.json' for bid in old}|{'data/members.json','data/stats.json'}
    hashes={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'data').rglob('*.json')}
    result={'base_sha':base,'members':len(current),'raw_arrays_and_provenance':'unchanged',
            'non_sec_components':'unchanged','non_sec_flags':'unchanged','replay':'identical',
            'raw_sec_count_preserved':raw_total,'eligible_sec_count':0,'score_changes':score_changes,
            'data_manifest_sha256':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()}
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
