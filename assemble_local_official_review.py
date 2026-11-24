#!/usr/bin/env python3
"""Create an isolated, unfinalized data snapshot from bill staging and pinned Git inputs."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from rebuild_official_corpus import sha_file, write_json

IDENTITY = ('name','party','state','district','chamber','photo_url','term_start','updated',
            'govtrack_id','bioguide_id')
FINANCE = ('total_raised','total_raised_display','pac_contributions','individual_contributions',
           'total_spent','cash_on_hand','fec_candidate_id','fec_cycle','fec_committee_ids')
DETAIL_SYNC = IDENTITY + FINANCE + ('trades','trades_updated','trade_count','latest_trade_date',
              'travel','travel_updated','travel_count','top_donors_list','top_donor_industries',
              'donors_updated')
SUMMARY_SYNC = IDENTITY + FINANCE + ('trade_count','latest_trade_date','travel_count')
ARCHIVE = ('bills','bills_updated','bills_count','bills_status','bills_analysis_status',
           'donor_alignment_score','donor_match_count','alec_match_count',
           'historical_legislation','amendments')
RAW_SEC = ('edgar_cik','edgar_status','edgar_signal_type','corporate_insider_signals','data_updated')
SAFE_ID = re.compile(r'[A-Z]\d{6}')


def git(*args):
    return subprocess.check_output(['git', *args])


def revision(arg):
    sha = git('rev-parse',f'{arg}^{{commit}}').decode().strip()
    if sha != arg or not re.fullmatch(r'[a-f0-9]{40}',sha):
        raise ValueError('explicit full immutable Git commits required')
    return sha


def load(ref, path):
    return json.loads(git('show',f'{ref}:{path}'))


def sync(output, old, latest, fields, counters):
    for key in fields:
        if key in latest:
            if latest[key] != old.get(key):
                counters[key] += 1
            output[key] = latest[key]


def base_row(old, latest, upstream, fields, counters):
    if old['id'] != latest['id']:
        raise ValueError('member identity drift')
    out = dict(old)
    sync(out, old, latest, fields, counters)
    # Keep quarantined identities/flags as-is; raw latest observations aren't
    # evidence of verified personal filings or safe score inputs.
    out['upstream_sec_observation'] = {
        'source_snapshot': upstream,
        'candidate_only': True,
        'fields': {k:latest[k] for k in RAW_SEC if k in latest}}
    return out


def assemble(staged, review, upstream, out):
    manifest = json.loads((staged / 'manifest.json').read_text())
    if manifest['scope'] != 'full_roster':
        raise ValueError('cannot assemble from a partial stage')
    review_roster = load(review,'data/members.json')
    upstream_raw = git('show',upstream+':data/members.json')
    if hashlib.sha256(upstream_raw).hexdigest() != manifest['roster_sha256']:
        raise ValueError('upstream roster does not match stage')
    newest = {r['id']:r for r in json.loads(upstream_raw)}
    ids = {r['id'] for r in review_roster}
    if (len(ids) != len(review_roster) or ids != newest.keys() or ids != manifest['member_sha256'].keys()
            or any(not SAFE_ID.fullmatch(i) for i in ids)):
        raise ValueError('roster drift; reconcile manually')
    if out.exists() or out.resolve().is_relative_to(Path('data').resolve()):
        raise ValueError('new isolated output required')
    members, changes = [], Counter()
    details_dir = out / 'data' / 'details'
    details_dir.mkdir(parents=True)
    for member in review_roster:
        bid = member['id']
        path = f'data/details/{bid}.json'
        old, latest = load(review,path), load(upstream,path)
        if latest.get('votes_status') != 'fetch_failed' or latest.get('votes') != old.get('votes'):
            raise ValueError(f'{bid}: vote records or outcome changed; reconcile deliberately')
        detail = base_row(old,latest,upstream,DETAIL_SYNC,changes)
        detail['votes_refresh_provenance'] = {
            'prior_review': old.get('votes_refresh_provenance'),
            'latest_attempt': {
                'source_snapshot': upstream,'checked': latest.get('votes_updated'),
                'failed_attempt_count': latest.get('votes_fail_count'),
                'status': latest.get('votes_status')}}
        detail['votes_checked'] = latest.get('votes_updated')
        detail['votes_fail_count'] = latest.get('votes_fail_count')
        detail['votes_updated'] = old.get('votes_updated')  # never mislabel the attempt as success
        latest_bill_fields = {k:latest[k] for k in ARCHIVE if k in latest}
        older_bill_fields = {k: old[k] for k in ARCHIVE if k in old}
        detail['bill_history'] = [
            {'source_snapshot': review,'publication_status':'legacy_archive_only','original_fields':older_bill_fields},
            {'source_snapshot': upstream,'publication_status':'legacy_archive_only','original_fields':latest_bill_fields},
        ]
        target = staged / (bid + '.json')
        if sha_file(target) != manifest['member_sha256'][bid]:
            raise ValueError(f'{bid}: staged hash mismatch')
        rebuilt = json.loads(target.read_text())
        if rebuilt['id'] != bid:
            raise ValueError(f'{bid}: wrong staged member')
        summary = base_row(member,newest[bid],upstream,SUMMARY_SYNC,changes)
        detail.update(rebuilt)
        # Staged first-party overlap is not ALEC or donor alignment.
        detail.update(alec_match_count=0,donor_match_count=0,donor_alignment_score=None,
                      donor_alignment_status='unavailable',
                      bill_source_manifest_sha256=manifest['official_manifest_sha256'],
                      bills_updated=None)
        summary.update(bills_count=rebuilt['bills_count'],
                       bills_analysis_status=rebuilt['bills_analysis_status'],
                       alec_match_count=0)
        write_json(detail,details_dir / (bid + '.json'))
        members.append(summary)
    previous = set(git('ls-tree','--name-only',review+':data/details').decode().splitlines())
    current = set(git('ls-tree','--name-only',upstream+':data/details').decode().splitlines())
    for name in sorted(previous | current):
        if not SAFE_ID.fullmatch(name.removesuffix('.json')) or name.removesuffix('.json') in ids:
            continue
        # Non-roster archival details do not get overwritten with active analysis.
        old = load(review,f'data/details/{name}') if name in previous else None
        new = load(upstream,f'data/details/{name}') if name in current else None
        if old is not None and new is not None and old != new:
            old = dict(old, archival_upstream_observation={'source_snapshot':upstream,'record':new})
        write_json(old if old is not None else new,details_dir / name)
    write_json(members, out / 'data' / 'members.json')
    write_json(load(upstream,'data/travel_cost_manifest.json'),out / 'data' / 'travel_cost_manifest.json')
    write_json({'readiness':'unfinalized_local_only_scores_and_aggregates_not_refreshed',
                'review_snapshot':review, 'upstream_snapshot':upstream,
                'staged_manifest_sha256':sha_file(staged / 'manifest.json'),
                'staged_members':len(members),'synced_fields':dict(changes)},out / 'assembly.json')
    print(f'{len(members)} members assembled; stale score and aggregate data must be regenerated before review.')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged',type=Path,required=True)
    parser.add_argument('--review',required=True)
    parser.add_argument('--upstream',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists() or args.out.resolve().is_relative_to(Path('data').resolve()):
        parser.error('--out must be a new isolated path outside the repository data tree')
    with tempfile.TemporaryDirectory(prefix='.review-assembly-',dir=args.out.parent) as temp:
        scratch = Path(temp) / 'snapshot'
        assemble(args.staged,revision(args.review),revision(args.upstream),scratch)
        scratch.rename(args.out)
