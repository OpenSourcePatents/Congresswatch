#!/usr/bin/env python3
"""Build and seal an isolated official bill snapshot; never contact or push Git."""
import argparse
from collections import Counter
import json
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

from assemble_local_official_review import ARCHIVE, SAFE_ID
from audit_staged_official_bills import audit as audit_evidence
from fetch_bills.utils.official_prose import ANALYSIS_VERSION
from rebuild_official_corpus import partitions, sha_file, write_json
from stage_official_bill_findings import inputs

ROOT = Path(__file__).resolve().parent
BASELINE = ROOT / 'benchmarks/bill_similarity/rebuild_source_manifest.json'
SCOPE = 'data/bill_refresh_manifest.json'
CATALOG = 'data/bill_refresh_catalog.json'
FIXED_PATHS = {'data/members.json','data/stats.json','data/bills.json','data/trades.json',SCOPE,CATALOG}


def fail(message):
    raise SystemExit('Bill refresh refused: ' + message)


def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])


def get(path):
    return json.loads(path.read_text())


def validate_live_scope(previous_congress, reviewed_congress, now):
    if previous_congress != reviewed_congress:
        fail('last good catalog Congress differs from the reviewed baseline')
    # Stop early at Jan 1 of the next transition year, instead of claiming
    # freshness across the Congress boundary before transition-specific review.
    if now.year > 1790 + (reviewed_congress - 1) * 2:
        fail('Congress rollover requires an explicitly reviewed source baseline')


def validate_sources(current, previous):
    congress = previous['congress']
    if current.get('congress') != congress or current.get('errors') != []:
        fail('unexpected Congress or failed official source scope')
    expected = {name for name,_,_ in partitions(congress)}
    for obj in (current, previous):
        names = [x['name'] for x in obj['partitions']]
        if len(names) != len(expected) or set(names) != expected:
            fail('missing or duplicate official source partitions')
    old = {x['name']:x for x in previous['partitions']}
    for part in current['partitions']:
        counts = part['counts']
        validated = counts.get('parsed', 0) + counts.get('amendment_instructions_excluded', 0)
        if counts.get('errors') or validated != part['indexed'] or part['indexed'] < old[part['name']]['indexed']:
            fail('incomplete or shrinking official partition: ' + part['name'])
    for key in ('official_metadata_bills','official_full_text_bills','official_full_text_versions'):
        if current[key] < previous[key]:
            fail('official source coverage shrank: ' + key)


def run(script, *args, cwd=ROOT):
    subprocess.run([sys.executable, str(ROOT / script), *map(str,args)], cwd=cwd, check=True)


def normalize(record, staged, source_digest, checked):
    if any(b.get('analysis_version') != ANALYSIS_VERSION for b in record.get('bills') or []):
        archive = {k:record[k] for k in ARCHIVE if k in record and k != 'bills'}
        archive['bills'] = [b for b in record['bills'] if b.get('analysis_version') != ANALYSIS_VERSION]
        record.setdefault('bill_history', []).append({
            'publication_status':'legacy_archive_only', 'original_fields':archive})
    record.update(staged,alec_match_count=0,donor_match_count=0,donor_alignment_score=None,
                  donor_alignment_status='unavailable',bill_source_manifest_sha256=source_digest,
                  bills_checked=checked,bills_updated=checked)


def quarantine_inactive(record):
    # Inactive profiles have no current sponsor analysis. Preserve historical
    # titles/records, but never flatten unverified legacy match fields back
    # into a freshly generated public aggregate.
    sensitive = ('keywords','has_text','similarity_score','alec_match','alec_best_similarity',
                 'similar_member_bills','match_type','donor_interest','text_source')
    bills = record.get('bills') or []
    indices = {i for i,bill in enumerate(bills)
               if bill.get('analysis_version') != ANALYSIS_VERSION
               and any(bill.get(field) for field in sensitive)}
    if not indices:
        return False
    record.setdefault('bill_history', []).append({
        'publication_status':'legacy_archive_only',
        'original_fields':{'bills':[bills[i] for i in sorted(indices)]}})
    replacement = dict(keywords=[],has_text=False,similarity_score=None,alec_match=None,
                       alec_best_similarity=None,similar_member_bills=[],match_type=None,
                       donor_interest=None,text_source=None,analysis_status='quarantined_legacy_corpus',
                       text_integrity='quarantined')
    record['bills'] = [dict(b, **replacement) if i in indices else b for i,b in enumerate(bills)]
    return True


def validate_assembled(data):
    summaries = {m['id']:m for m in get(data/'members.json')}
    stats = get(data/'stats.json')
    if (stats['total_members'] != len(summaries)
            or stats['members_with_scores'] != sum(m['score'] > 0 for m in summaries.values())
            or stats['high_anomaly'] != sum(m['score'] >= 60 for m in summaries.values())):
        fail('aggregate score totals differ from the roster')
    expected_bills, expected_trades, source_matches = Counter(), Counter(), {}
    for path in (data/'details').glob('*.json'):
        detail = get(path)
        bid = path.stem
        for bill in detail.get('bills') or []:
            key = (bid,bill.get('bill_id'))
            expected_bills[key] += 1
            if bill.get('analysis_version') == ANALYSIS_VERSION:
                source_matches[key] = {m['bill_id']:m['evidence'] for m in bill['similar_member_bills']}
        expected_trades[bid] += len(detail.get('trades') or [])
    bills = get(data/'bills.json')
    if expected_bills != Counter((row.get('bioguide_id'),row.get('bill_id')) for row in bills):
        fail('flat bill aggregate does not match detail identities')
    if +expected_trades != Counter(row.get('bioguide_id') for row in get(data/'trades.json')):
        fail('flat trade aggregate does not match detail coverage')
    for row in bills:
        if row.get('analysis_version') != ANALYSIS_VERSION:
            continue
        bid, bill = row['bioguide_id'], row['bill_id']
        expected = source_matches[(bid,bill)]
        matches = row['similar_member_bills']
        if row.get('detail_path') != f'data/details/{bid}.json' or set(expected) != {m['bill_id'] for m in matches}:
            fail('flat official bill aggregate is missing detail links or matching identities')
        for m in matches:
            expected_evidence = {k:v for k,v in expected[m['bill_id']].items()
                                 if k not in ('target_passage','matched_passage')}
            if m['evidence'] != expected_evidence:
                fail('flat official bill aggregate contains inconsistent fingerprints')


def digest_files(root):
    expected = FIXED_PATHS | {str(p.relative_to(root)) for p in (root/'data/details').glob('*.json')}
    if any(path not in FIXED_PATHS and not SAFE_ID.fullmatch(Path(path).stem) for path in expected):
        fail('unexpected detail path')
    return {p:sha_file(root/p) for p in sorted(expected)}


def prepare(args, sha):
    if args.out.exists() or args.out.resolve().is_relative_to(ROOT/'data'):
        fail('isolated new output directory required')
    if args.saved_stage and not args.saved_cache:
        fail('a saved stage requires the saved official source cache')
    with tempfile.TemporaryDirectory(prefix='official-bills-',dir=args.out.parent) as temp:
        work = Path(temp)
        candidate = work/'candidate'
        candidate.mkdir()
        # Git's tree snapshot is immutable; no copy of an earlier worktree or user edits.
        subprocess.run(['tar','-xf','-','-C',str(candidate)],input=git('archive',sha,'data'),check=True)
        data = candidate/'data'
        baseline = get(BASELINE)
        if (candidate/SCOPE).exists() != (candidate/CATALOG).exists():
            fail('existing source manifest and catalog must both exist')
        prior_refresh = get(candidate/SCOPE) if (candidate/SCOPE).exists() else None
        previous = prior_refresh['sources'] if prior_refresh else baseline
        if prior_refresh and prior_refresh.get('catalog_sha256') != sha_file(candidate/CATALOG):
            fail('last good source catalog does not match its manifest')
        cache = args.saved_cache or work/'govinfo'
        if not args.saved_cache:
            validate_live_scope(previous['congress'],baseline['congress'],datetime.now(timezone.utc))
            run('rebuild_official_corpus.py','--cache',cache,'--congress',previous['congress'],'--refresh')
        source = get(cache/'manifest.json')
        validate_sources(source, baseline)
        validate_sources(source, previous)
        corpus, status, bound_source = inputs(cache)
        if bound_source != source:
            fail('source manifest changed during preflight')
        catalog = {'body_ids':sorted(corpus),'metadata_ids':sorted(status)}
        del corpus,status
        if (candidate/CATALOG).exists():
            old_catalog = get(candidate/CATALOG)
            for field,ids in catalog.items():
                if not set(old_catalog[field]) <= set(ids):
                    fail('official source identities disappeared: ' + field)
        staged = args.saved_stage or work/'stage'
        if not args.saved_stage:
            run('stage_official_bill_findings.py','--cache',cache,'--roster',data/'members.json','--output',staged)
        stage = get(staged/'manifest.json')
        members = get(data/'members.json')
        keys = [m['id'] for m in members]
        if (not keys or len(set(keys)) != len(keys) or any(not SAFE_ID.fullmatch(k) for k in keys)
                or stage['scope'] != 'full_roster' or set(keys) != stage['member_sha256'].keys()
                or stage['roster_sha256'] != sha_file(data/'members.json')
                or stage['records'].get('text_source_unavailable')):
            fail('staging coverage, requested text or roster does not match the pinned input')
        audit_evidence(staged,cache)
        protected = ('votes', 'trades', 'travel', 'top_donors_list', 'top_donor_industries',
                     'votes_updated','trades_updated','travel_updated','donors_updated',
                     'edgar_reconciliation','upstream_sec_observation','other_filing_links')
        originals = {}
        for member in members:
            key = member['id']
            path = data/'details'/f'{key}.json'
            record = get(path)
            new = get(staged/f'{key}.json')
            if record.get('id') != key or new['id'] != key:
                fail('member detail identity mismatch')
            originals[key] = {f:record[f] for f in protected if f in record}
            normalize(record,new,stage['official_manifest_sha256'],source['checked_at'])
            member.update(bills_count=new['bills_count'],bills_analysis_status=new['bills_analysis_status'],
                          bills_analysis_version=ANALYSIS_VERSION,alec_match_count=0)
            write_json(record,path)
        for path in sorted((data/'details').glob('*.json')):
            if path.stem not in originals:
                record = get(path)
                if quarantine_inactive(record):
                    write_json(record,path)
        write_json(members,data/'members.json')
        # audit_data.py resolves data relative to its file, not the current directory.
        for script in ('recompute_scores.py','build_aggregates.py'):
            run(script,cwd=candidate)
        validate_assembled(data)
        for key in keys:
            record = get(data/'details'/f'{key}.json')
            if {f:record[f] for f in protected if f in record} != originals[key]:
                fail('non-bill source projection changed: ' + key)
        shutil.copyfile(ROOT/'audit_data.py',candidate/'audit_data.py')
        exec_env = dict(os.environ)
        exec_env['PYTHONPATH'] = str(ROOT) + (':' + exec_env['PYTHONPATH'] if exec_env.get('PYTHONPATH') else '')
        with (work/'data-audit.json').open('wb') as report:
            subprocess.run([sys.executable,str(candidate/'audit_data.py')],cwd=candidate,
                           env=exec_env,check=True,stdout=report)
        (candidate/'audit_data.py').unlink()
        write_json(catalog,candidate/CATALOG)
        write_json({'source_commit':sha,'analysis_version':ANALYSIS_VERSION,'sources':source,
                    'stage_sha256':sha_file(staged/'manifest.json'),'catalog_sha256':sha_file(candidate/CATALOG),
                    'members':len(keys),'records':stage['records'],'matches':stage['matches']},candidate/SCOPE)
        write_json({'source_commit':sha,'files':digest_files(candidate),
                    'offline_only':bool(args.saved_cache or args.saved_stage)},candidate/'verified.json')
        candidate.rename(args.out)
        print(f'Validated {len(keys)} member records in isolated snapshot: {args.out}',flush=True)


def install(path,sha):
    seal = get(path/'verified.json')
    if seal.get('offline_only') or seal.get('source_commit') != sha or digest_files(path) != seal.get('files'):
        fail('candidate was not built with live sources, input changed or staged hashes differ')
    for name in seal['files']:
        dest = ROOT/name
        temporary = dest.with_suffix(dest.suffix+'.refresh-tmp')
        shutil.copyfile(path/name,temporary)
        temporary.replace(dest)


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--expected-head',required=True)
    parser.add_argument('--saved-cache',type=Path,help='offline test only; cannot install')
    parser.add_argument('--saved-stage',type=Path,help='offline test only; independently audited')
    parser.add_argument('--install',action='store_true')
    parser.add_argument('--saved-input',help='offline test only: explicit saved input commit')
    args = parser.parse_args()
    args.out = args.out.resolve()
    sha = git('rev-parse','HEAD').decode().strip()
    if sha != args.expected_head or not re.fullmatch(r'[a-f0-9]{40}',sha):
        fail('pinned checkout SHA differs from HEAD')
    if args.saved_input:
        if not args.saved_cache or args.install or not re.fullmatch(r'[a-f0-9]{40}',args.saved_input):
            fail('saved input mode requires a full commit ID and an offline cache')
        if git('rev-parse', args.saved_input+'^{commit}').decode().strip() != args.saved_input:
            fail('saved input did not resolve to the requested commit')
        sha = args.saved_input
    if args.install:
        if args.saved_cache or args.saved_stage or git('status','--porcelain','--untracked-files=no').strip():
            fail('install requires a clean, pinned worktree and live-source candidate')
        install(args.out,sha)
    else:
        prepare(args,sha)


if __name__ == '__main__':
    cli()
