#!/usr/bin/env python3
"""Read-only audit of all staged provenance, output hashes and quoted passages."""
import argparse
from collections import Counter
from functools import lru_cache
import json
import math
from pathlib import Path
from fetch_bills.utils.official_prose import ANALYSIS_VERSION
from fetch_bills.utils.official_passages import Tokens, MIN_COSINE, MIN_COVERAGE, MIN_PASSAGE_WORDS
from rebuild_official_corpus import ROOT_PATH, sha_file
from stage_official_bill_findings import inputs


def audit(output, cache, checkpoint=False):
    corpus, status, source_manifest = inputs(cache)
    progress = json.loads((output / 'progress.json').read_text())
    run = progress['run']
    if checkpoint:
        manifest = run | {'scope': 'incomplete_checkpoint'}
        files = progress['completed']
        if not files.keys() <= set(run['member_ids']):
            raise ValueError('unexpected member checkpoint IDs')
    else:
        manifest = json.loads((output / 'manifest.json').read_text())
        files = manifest['member_sha256']
        if len(files) != manifest['members'] or progress['completed'] != files or set(run['member_ids']) != set(files):
            raise ValueError('incomplete staged file manifest / invalid checkpoint bindings')
    if (manifest['corpus_sha256'] != source_manifest['corpus_sha256']
            or manifest['official_manifest_sha256'] != sha_file(cache / 'manifest.json')):
        raise ValueError('audit does not use the staged corpus hash')
    get_tokens = lru_cache(maxsize=96)(lambda key: Tokens.from_text(corpus[key]['text']))
    observed = Counter()
    matches = 0
    for member, digest in sorted(files.items()):
        file = output / (member + '.json')
        if sha_file(file) != digest:
            raise ValueError(f'{member}: stale member checkpoint')
        data = json.loads(file.read_text())
        ids = [b['bill_id'] for b in data['bills']]
        if data['id'] != member or len(set(ids)) != len(ids) or len(ids) != data['bills_count']:
            raise ValueError(f'{member}: incomplete or duplicated bill identities')
        for bill in data['bills']:
            k = bill['bill_id']
            expected = status[k]
            if bill['sponsor_id'] != member or member not in expected['sponsor_ids']:
                raise ValueError(f'{member}/{k}: unauthorized sponsor association')
            if (f"{bill['type']}{bill['number']}-{bill['congress']}" != k
                    or type(bill['congress']) is not int or type(bill['number']) is not str
                    or bill['title'] != expected['title']
                    or bill['introduced_date'] != expected['introduced_date']
                    or bill['latest_action'] != expected['latest_action']
                    or bill['cosponsors'] != expected['cosponsor_ids']):
                raise ValueError(f'{member}/{k}: bill contract or source mismatch')
            if bill['analysis_version'] != ANALYSIS_VERSION or bill.get('alec_match') or bill.get('donor_interest'):
                raise ValueError(f'{member}/{k}: unexpected model or donor claim')
            if k in corpus and bill.get('text_source') != corpus[k]['source']:
                raise ValueError(f'{member}/{k}: target source fingerprint differs')
            findings = bill['similar_member_bills']
            if len(findings) > 3 or (findings and bill['analysis_status'] != 'analyzed'):
                raise ValueError(f'{member}/{k}: invalid results count/status')
            seen = {k}
            for match in findings:
                other = match['bill_id']
                if other in seen:
                    raise ValueError(f'{member}/{k}: invalid self/duplicate result {other}')
                seen.add(other)
                ev, cosine = match['evidence'], match['similarity_score']
                if not math.isfinite(cosine) or not MIN_COSINE <= cosine <= 1:
                    raise ValueError(f'{member}/{k}/{other}: cosine out of bounds')
                total, shown = ev['longest_passage_words'], ev['displayed_passage_words']
                coverage = [ev[x+'_shingle_coverage'] for x in ('target','matched')]
                if total < MIN_PASSAGE_WORDS or shown != min(total, 120) or not all(
                        0 <= x <= 1 for x in coverage) or max(coverage) < MIN_COVERAGE:
                    raise ValueError(f'{member}/{k}/{other}: evidence thresholds not met')
                terms = []
                for side, key in [('target',k),('matched',other)]:
                    source = corpus[key]['source']
                    for field in ('source_url','source_sha256','text_sha256','version_code','version_date'):
                        if ev[side+'_'+field] != source[field]:
                            raise ValueError(f'{member}/{k}/{other}: evidence source fingerprint differs')
                    offset, quote = ev[side+'_word_offset'], ev[side+'_passage']
                    tokens = get_tokens(key)
                    if not 0 <= offset <= len(tokens.terms) - total or quote != tokens.excerpt(offset, shown):
                        raise ValueError(f'{member}/{k}/{other}: displayed passage does not resolve to source')
                    terms.append(tokens.terms[offset:offset+total])
                if terms[0] != terms[1]:
                    raise ValueError(f'{member}/{k}/{other}: claimed shared passage not present')
                matches += 1
            observed[bill['analysis_status']] += 1
    if not checkpoint and (dict(observed) != manifest['records'] or matches != manifest['matches']):
        raise ValueError('manifest counts do not match member data')
    print(json.dumps({'scope':manifest['scope'], 'members':len(files),
                      'records': observed, 'verified_matches': matches}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('staged_dir', type=Path)
    parser.add_argument('--cache', type=Path, default=ROOT_PATH)
    parser.add_argument('--checkpoint', action='store_true', help='verified completed subset only')
    opts = parser.parse_args()
    audit(opts.staged_dir, opts.cache, opts.checkpoint)
