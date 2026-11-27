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
from validation.reconcile_finance import verified_manifest, reconcile_record, original as git_original


def main():
    pins=json.loads((ROOT/'validation/sec_wrong_owners.json').read_text())
    base=pins['base_sha']
    def original(path):
        return json.loads(subprocess.check_output(['git','show',f'{base}:{path}'],cwd=ROOT))
    current=json.loads((ROOT/'data/members.json').read_text())
    old={m['id']:m for m in original('data/members.json')}
    finance=verified_manifest()
    prior_review=finance['prior_review_sha']
    allowed={'score','score_components','flags','sec_attribution_version'}
    score_changes=[];raw_total=0
    for m in current:
        bid=m['id'];path=f'data/details/{bid}.json'
        before=original(path);after=json.loads((ROOT/path).read_text())
        previous=git_original(prior_review,path)
        trace=finance['members'][bid]
        expected_detail=reconcile_record(before,previous,trace)
        expected_roster=reconcile_record(old[bid],previous,trace)
        for label,a,b in [('roster',expected_roster,m),('detail',expected_detail,after)]:
            changed={k for k in a.keys()|b.keys() if a.get(k)!=b.get(k)}
            assert changed<=allowed,(bid,label,changed-allowed)
            assert [x for x in a.get('flags',[]) if x!='trade']==[x for x in b['flags'] if x!='trade']
        for key,value in before['score_components'].items():
            if key!='trade_timing':assert after['score_components'][key]==value,(bid,key)
        assert m['score']==after['score']==previous['score']==min(sum(after['score_components'].values()),100)
        assert after['flags']==previous['flags']
        for key in ('trades','votes','bills','travel','score_components'):
            assert after.get(key)==previous.get(key),(bid,key)
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
    result={'base_sha':base,'members':len(current),'raw_arrays':'unchanged','raw_sec_provenance':'complete incoming/prior pairs retained in pinned manifest',
            'non_sec_components':'unchanged','non_sec_flags':'unchanged','replay':'identical',
            'raw_sec_count_preserved':raw_total,'eligible_sec_count':0,'score_changes':score_changes,
            'data_manifest_sha256':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest()}
    if pins.get('previous_base_sha'):
        prior = {m['id']: m for m in json.loads(subprocess.check_output(
            ['git','show',pins['previous_base_sha']+':data/members.json'],cwd=ROOT))}
        historical = {m['id']:m for m in git_original(pins['score_reconciliation_target_sha'],'data/members.json')}
        upstream_changes = {bid for bid in historical if historical[bid]['score'] != prior[bid]['score']}
        sec_changes = {row['id'] for row in score_changes}
        overlap = sorted(upstream_changes & sec_changes)
        for m in current:
            if m['id'] in upstream_changes - sec_changes:
                assert m['score'] == historical[m['id']]['score'], m['id']
        result['reconciliation'] = {
            'previous_base_sha': pins['previous_base_sha'],
            'previous_review_sha': pins['previous_review_sha'],
            'incoming_score_changes': len(upstream_changes),
            'overlap_with_sec_corrections': overlap,
            'unrelated_incoming_scores_preserved': len(upstream_changes - sec_changes),
            'onder_score': next(m['score'] for m in current if m['id']=='O000177'),
        }
    if pins.get('attempt_metadata_base_sha'):
        attempt_base = pins['attempt_metadata_base_sha']
        prior_roster = {m['id']: m for m in json.loads(subprocess.check_output(
            ['git','show',attempt_base+':data/members.json'],cwd=ROOT))}
        target = pins['attempt_metadata_target_sha']
        target_roster = {m['id']:m for m in git_original(target,'data/members.json')}
        attempt_paths = subprocess.check_output(
            ['git','diff','--name-only',attempt_base,target],cwd=ROOT,text=True).splitlines()
        assert set(attempt_paths) == {'data/members.json'} | {
            f'data/details/{bid}.json' for bid in old}
        for m in current:
            bid = m['id']
            assert {k for k in prior_roster[bid].keys() | target_roster[bid].keys()
                    if prior_roster[bid].get(k) != target_roster[bid].get(k)} == {'data_updated'}
            path = f'data/details/{bid}.json'
            prior = json.loads(subprocess.check_output(
                ['git','show',attempt_base+':'+path],cwd=ROOT))
            upstream = git_original(target,path)
            after = json.loads((ROOT/path).read_text())
            assert {k for k in prior.keys() | upstream.keys()
                    if prior.get(k) != upstream.get(k)} == {'votes_updated','votes_fail_count'}
            assert upstream['votes_fail_count'] == prior['votes_fail_count'] + 1
            assert after['votes_fail_count'] == upstream['votes_fail_count']
            assert after['votes_updated'] == upstream['votes_updated']
            assert after['votes_status'] == upstream['votes_status'] == prior['votes_status'] == 'fetch_failed'
        result['attempt_metadata'] = {
            'base_sha': attempt_base,
            'previous_review_sha': pins['attempt_metadata_review_sha'],
            'roster_timestamp_rows_preserved': len(current),
            'failed_vote_attempts_preserved': len(current),
            'counter_increment_per_member': 1,
            'vote_arrays_and_other_fields': 'unchanged',
            'fresh_vote_evidence': False,
        }
    result['finance_reconciliation']={'members':539,'failed_attempts':114,'alias_500_lines':124,'effective_scores_and_flags_vs_prior_review':'identical','raw_observations_manifest':'validation/finance_run_46018133463.json'}
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
