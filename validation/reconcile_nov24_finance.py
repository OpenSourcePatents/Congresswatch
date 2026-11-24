"""Bounded replay of the 16:22 finance commit; no network or collector execution."""
import copy
from collections import Counter
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess

BASE = 'd1e83aa7bf0a90aa135a5a8d8020e26a5606c0a4'
REVIEW = 'b0faa5a150e97733ddce4dc3201ce115717521d2'
MAIN = '1f757a012d0831f97f5e56dbea68b5f76999ffd3'
RUN = 45426256499
EVIDENCE = Path('benchmarks/nov24-finance-run-evidence.json')
RAW = ('edgar_cik', 'corporate_insider_signals', 'edgar_status', 'data_updated',
       'last_updated', 'score', 'score_components', 'flags')
SEC = ('edgar_cik', 'corporate_insider_signals', 'edgar_status', 'edgar_data_status',
       'edgar_checked', 'edgar_updated')


def git(*args):
    return subprocess.check_output(['git', *args])


@lru_cache(maxsize=4096)
def read(ref, path):
    return json.loads(git('show', ref + ':' + path))


def projection(record, fields):
    return {k: copy.deepcopy(record[k]) for k in fields if k in record}


def changed(a, b):
    return {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}


def reconciled_sec(prior, observation, checked):
    # This legacy collector supplies no identity/evidence receipts. Never replace
    # an independently reviewed identity with an automatic observation.
    if prior.get('edgar_identity_status') == 'verified':
        raise ValueError('Verified identity requires separate reviewed reconciliation')
    result = projection(prior, SEC)
    result['edgar_checked'] = checked
    if observation['alias_errors'] or observation['query_errors']:
        result['edgar_status'] = 'query_failed'
        result['edgar_data_status'] = ('stale' if prior.get('edgar_cik') and
            prior.get('corporate_insider_signals') is not None else 'unavailable')
        # Retain the previous CIK/count pair and successful-retrieval timestamp.
    elif observation['hit']:
        hit = observation['hit']
        result.update(edgar_cik=hit['cik'], corporate_insider_signals=hit['count'],
                      edgar_status='unverified_auto', edgar_data_status='available',
                      edgar_updated=checked)
    else:
        assert not prior.get('edgar_cik'), 'Do not discard a historical paired observation'
        result.update(edgar_cik=None, corporate_insider_signals=0,
                      edgar_status=observation['status'], edgar_data_status='unresolved')
    return result


def main():
    evidence = json.loads(EVIDENCE.read_text())
    assert evidence['run'] == RUN and evidence['source_snapshot'] == MAIN
    by_id = evidence['members']
    base, review, incoming = [read(ref, 'data/members.json') for ref in (BASE, REVIEW, MAIN)]
    maps = [{m['id']: m for m in rows} for rows in (base, review, incoming)]
    assert all(len(m) == 539 for m in maps) and maps[0].keys() == maps[1].keys() == maps[2].keys() == by_id.keys()
    paths = set(git('diff', '--name-only', BASE, MAIN).decode().splitlines())
    assert paths == {'data/members.json', 'data/stats.json'} | {f'data/details/{bid}.json' for bid in maps[0]}
    detail_counts, roster_counts, outcomes = Counter(), Counter(), Counter()
    outputs, members = {}, []
    for old_member in review:
        bid = old_member['id']
        path = f'data/details/{bid}.json'
        old, prior, new = [read(ref, path) for ref in (BASE, REVIEW, MAIN)]
        dc = changed(old, new); rc = changed(maps[0][bid], maps[2][bid])
        assert dc <= {'last_updated','data_updated','edgar_cik','edgar_status','corporate_insider_signals','score','score_components','flags'}, (bid,dc)
        assert rc <= {'data_updated','edgar_cik','edgar_status','corporate_insider_signals','score','flags'}, (bid,rc)
        detail_counts.update(dc); roster_counts.update(rc)
        observation = by_id[bid]
        checked = new['last_updated']
        assert checked == new['data_updated'] == maps[2][bid]['data_updated']
        if observation['hit']:
            hit = observation['hit']
            assert new['edgar_cik'] == maps[2][bid]['edgar_cik'] == hit['cik']
            assert new['corporate_insider_signals'] == maps[2][bid]['corporate_insider_signals'] == hit['count']
        else:
            assert observation['status'] == new['edgar_status']
        result = copy.deepcopy(prior)
        result.update(reconciled_sec(prior, observation, checked))
        result['finance_refresh_reconciliation'] = {
            'source_snapshot': MAIN, 'prior_upstream_snapshot': BASE, 'review_snapshot': REVIEW,
            'run': RUN, 'job': evidence['job'], 'evidence_file': str(EVIDENCE),
            'evidence_sha256': hashlib.sha256(EVIDENCE.read_bytes()).hexdigest(),
            'checked': checked,
            'checked_semantics': 'Legacy member processing timestamp, not proof of fresh FEC or successful SEC evidence.',
            'observed_roster': projection(maps[2][bid], RAW),
            'observed_detail': projection(new, RAW),
            'previous_reviewed_sec': projection(prior, SEC),
            'candidate_only': True,
            'alias_error_count': len(observation['alias_errors']),
            'filing_query_error_count': len(observation['query_errors']),
            'successful_candidate_count_response': observation['hit'],
            'incomplete_attempt': bool(observation['alias_errors'] or observation['query_errors']),
            'derived_upstream_values_applied': False}
        member = copy.deepcopy(old_member)
        for field in SEC:
            if field in result:
                member[field] = copy.deepcopy(result[field])
        member['finance_refresh_source_snapshot'] = MAIN
        # Keep identities, receipts, finance facts, general freshness and every raw
        # non-SEC array from the reviewed input; only the classified SEC fields move.
        assert changed(prior, result) <= set(SEC) | {'finance_refresh_reconciliation'}
        assert changed(old_member, member) <= set(SEC) | {'finance_refresh_source_snapshot'}
        members.append(member); outputs[path] = result
        outcome = ('incomplete_attempt' if observation['alias_errors'] or observation['query_errors']
                   else 'complete_candidate_count' if observation['hit'] else 'complete_no_candidate_count')
        outcomes[outcome] += 1
    assert detail_counts == Counter(last_updated=539,data_updated=539,score_components=6,edgar_status=16,flags=5,corporate_insider_signals=8,score=6,edgar_cik=3)
    assert roster_counts == Counter(data_updated=539,flags=5,corporate_insider_signals=8,edgar_status=16,score=6,edgar_cik=9)
    assert changed(read(BASE,'data/stats.json'),read(MAIN,'data/stats.json')) == {'last_updated','total_insider_signals'}
    assert outcomes == Counter(incomplete_attempt=247,complete_candidate_count=14,complete_no_candidate_count=278)
    outputs['data/members.json'] = members
    report = {'base':BASE,'review':REVIEW,'main':MAIN,'run':RUN,'incoming_paths':len(paths),
              'detail_field_changes':dict(sorted(detail_counts.items())),'roster_field_changes':dict(sorted(roster_counts.items())),
              'outcomes':dict(outcomes),'upstream_derived_fields_imported':False,
              'all_non_sec_inputs_and_identity_evidence_preserved':True,
              'upstream_summary':read(MAIN,'data/stats.json')}
    outputs['benchmarks/nov24-finance-reconciliation.json'] = report
    changed_files = 0
    for path, value in outputs.items():
        if path.startswith('data/'):
            assert json.loads(Path(path).read_bytes()) in (read(REVIEW,path),value), ('unexpected local edits',path)
        encoded = (json.dumps(value,indent=2,ensure_ascii=True)+'\n').encode()
        changed_files += not Path(path).exists() or Path(path).read_bytes() != encoded
        Path(path).write_bytes(encoded)
    print(json.dumps({'changed_files':changed_files,'outcomes':dict(outcomes)}))


if __name__ == '__main__':
    main()
