#!/usr/bin/env python3
"""Calculate bill-only artifacts in an isolated output directory; no publishes."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from fetch_bills.utils.official_prose import ANALYSIS_VERSION, sha
from fetch_bills.utils.official_passages import MIN_PASSAGE_WORDS
from fetch_bills.utils.official_retrieval import MODEL_UNAVAILABLE, OfficialRetrieval
from rebuild_official_corpus import ROOT_PATH, sha_file, write_json


def inputs(cache):
    manifest = json.loads((cache / 'manifest.json').read_text())
    if manifest.get('errors') or len(manifest.get('partitions', [])) != 24:
        raise SystemExit('Incomplete GovInfo corpus manifest; refusing analysis')
    parsed = []
    for name in ('corpus', 'status'):
        path = cache / (name + '.json')
        if manifest.get(name + '_sha256') != sha_file(path):
            raise SystemExit(f'{name}: corpus manifest hash mismatch')
        parsed.append(json.loads(path.read_text()))
    corpus, metadata = parsed
    for key, row in corpus.items():
        if row['source']['bill_id'] != key or row['source']['text_sha256'] != sha(row['text'].encode()):
            raise SystemExit(f'{key}: unverified bill body')
    return corpus, metadata, manifest


def bill_record(source, bill_id, corpus, engine, matches, sponsor):
    record = {'bill_id': bill_id, 'title': source['title'], 'introduced_date': source['introduced_date'],
              'url': source.get('legislation_url'), 'latest_action': source['latest_action'],
              'latest_action_date': source['latest_action_date'], 'cosponsors': source['cosponsor_ids'],
              'sponsor_id': sponsor, 'sponsor_verification': 'official_bioguide_match',
              'metadata_source_url': source['source_url'], 'metadata_source_sha256': source['source_sha256'],
              'analysis_version': ANALYSIS_VERSION, 'model_analysis_status': MODEL_UNAVAILABLE,
              'similar_member_bills': []}
    if bill_id in corpus:
        record['text_source'] = corpus[bill_id]['source']
        record['has_text'] = True
        if len(engine.tokens(engine.indices[bill_id]).terms) < MIN_PASSAGE_WORDS:
            record['analysis_status'] = 'insufficient_text_for_overlap'
        else:
            record.update(analysis_status='analyzed', similar_member_bills=matches[bill_id])
    else:
        record.update(has_text=False, analysis_status=(
            'text_source_unavailable' if source['versions'] else 'text_not_yet_published'))
    return record


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--cache', type=Path, default=ROOT_PATH)
    cli.add_argument('--roster', type=Path, default=Path('data/members.json'))
    cli.add_argument('--output', type=Path, required=True)
    cli.add_argument('--member', action='append', default=[], help='optional subset; does not claim full coverage')
    cli.add_argument('--resume', action='store_true', help='resume verified per-member checkpoints')
    opts = cli.parse_args()
    if opts.output.resolve().is_relative_to(Path('data').resolve()):
        raise SystemExit('Output must not be the repository data directory')
    if opts.output.exists() != opts.resume:
        raise SystemExit('New output path required unless explicitly resuming a checkpoint')
    roster = json.loads(opts.roster.read_text())
    roster_ids = {m['id'] for m in roster}
    members = set(opts.member) if opts.member else roster_ids
    if not members or not members <= roster_ids:
        raise SystemExit('Empty roster or unknown member selection')
    corpus, metadata, manifest = inputs(opts.cache)
    authored = defaultdict(list)
    for key, row in metadata.items():
        for sponsor in set(row['sponsor_ids']) & members:
            authored[sponsor].append(key)
    files = ('stage_official_bill_findings.py', 'fetch_bills/utils/official_prose.py',
             'fetch_bills/utils/official_passages.py', 'fetch_bills/utils/official_retrieval.py')
    import numpy, scipy, sklearn
    checkpoint = opts.output / 'progress.json'
    run = {'analysis_version': ANALYSIS_VERSION, 'scope': 'sample' if opts.member else 'full_roster',
        'member_ids': sorted(members),
        'roster_sha256': sha_file(opts.roster), 'official_manifest_sha256':sha_file(opts.cache / 'manifest.json'),
        'corpus_sha256': manifest['corpus_sha256'],
        'software': {p:sha_file(Path(p)) for p in files},
        'versions': {m.__name__:m.__version__ for m in (numpy, scipy, sklearn)}}
    completed = {}
    if opts.resume:
        previous = json.loads(checkpoint.read_text())
        if previous.get('run') != run:
            raise SystemExit('Checkpoint inputs changed; start a new staging directory')
        completed = previous['completed']
        if not set(completed) <= members or any(
                sha_file(opts.output / (bid + '.json')) != digest for bid,digest in completed.items()):
            raise SystemExit('Checkpoint output hashes differ')
    else:
        opts.output.mkdir()
        write_json({'run':run,'completed':completed}, checkpoint)

    if set(completed) < members:
        print(f'Indexing {len(corpus)} bodies; {len(completed)}/{len(members)} member checkpoints verified', flush=True)
        engine = OfficialRetrieval(corpus, metadata)
        for member in sorted(members - set(completed)):
            keys = sorted(authored[member], key=lambda k: (metadata[k]['introduced_date'] or '',k), reverse=True)
            queries = [engine.indices[key] for key in keys if key in engine.indices]
            matches = {engine.keys[idx]:engine.evidence(idx,ranked)
                       for idx,ranked in engine.candidates(queries)}
            rows = [bill_record(metadata[k],k,corpus,engine,matches,member) for k in keys]
            target = opts.output / (member + '.json')
            write_json({'id': member, 'bills': rows, 'bills_count': len(rows),
                'bills_analysis_version': ANALYSIS_VERSION, 'bills_status':'available' if rows else 'empty',
                'bills_analysis_status':'analyzed' if rows else 'empty', 'model_analysis_status': MODEL_UNAVAILABLE,
                'donor_alignment_status': 'unavailable'}, target)
            completed[member] = sha_file(target)
            write_json({'run':run,'completed':completed}, checkpoint)
            print(f'{len(completed)}/{len(members)}: {member} ({len(rows)} records)', flush=True)

    counts = Counter()
    total_matches = 0
    for member in sorted(members):
        rows = json.loads((opts.output / (member + '.json')).read_text())['bills']
        counts.update(row['analysis_status'] for row in rows)
        total_matches += sum(len(row['similar_member_bills']) for row in rows)
    result = {k:v for k,v in run.items() if k != 'member_ids'}
    result.update(members=len(members), records=dict(counts), matches=total_matches,
                  member_sha256=completed)
    write_json(result, opts.output / 'manifest.json')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
