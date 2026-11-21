"""Deterministic latest-version retrieval replay on actual official CRS links."""
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from fetch_bills.utils.official_retrieval import OfficialRetrieval
from fetch_bills.utils.official_passages import Passages, MIN_COSINE, MIN_PASSAGE_WORDS
from rebuild_official_corpus import ROOT_PATH, write_json
from stage_official_bill_findings import inputs


def main(output):
    corpus, meta, manifest = inputs(ROOT_PATH)
    pairs = sorted({tuple(sorted((key, other)))
                    for key, record in meta.items()
                    for other in record.get('crs_identical_bills', [])
                    if key in corpus and other in corpus and other != key},
                    key=lambda pair: sha256(('review-v2:'+':'.join(pair)).encode()).hexdigest())
    if len(pairs) < 200:
        raise SystemExit('Missing enough official CRS identical pairs for evaluation')
    # Set selection independent of scores; do not tune against these outcomes.
    frozen = pairs[:200]
    targets = defaultdict(list)
    for target, matched in frozen:
        targets[target].append(matched)
    engine = OfficialRetrieval(corpus,meta)
    passed = 0
    miss_reasons = Counter()
    outcomes = []
    for idx, candidates in engine.candidates([engine.indices[k] for k in sorted(targets)]):
        target = engine.keys[idx]
        found = [m['bill_id'] for m in engine.evidence(idx,candidates)]
        for other in targets[target]:
            passed += other in found
            item = {'target':target,'expected':other,'ranked_matches':found,
                'target_source':corpus[target]['source'],'expected_source':corpus[other]['source']}
            if other not in found:
                m_idx = engine.indices[other]
                cosine = float(engine.matrix[idx].multiply(engine.matrix[m_idx]).sum())
                pair = Passages(engine.tokens(idx))
                matched = engine.tokens(m_idx)
                longest, *_ = pair.longest(matched)
                if cosine < MIN_COSINE:
                    reason = 'cosine_below_threshold'
                elif longest < MIN_PASSAGE_WORDS:
                    reason = 'shared_passage_under_32_words'
                elif pair.evidence(matched) is None:
                    reason = 'relative_shingle_coverage_below_threshold'
                elif other not in dict(candidates):
                    reason = 'qualified_but_outside_candidate_cap'
                elif len(found) >= 3:
                    reason = 'qualified_but_below_top_three'
                else:
                    reason = 'unexpected_inconsistent_gate'
                miss_reasons[reason] += 1
                item.update(reason=reason, pair_cosine=cosine, pair_longest_words=longest)
            outcomes.append(item)
    result = {'corpus_sha256': manifest['corpus_sha256'], 'status_sha256': manifest['status_sha256'],
              'eligible_pairs': len(pairs), 'selection': 'First 200 of sorted sha256(review-v2:LEFT:RIGHT)',
              'positive_pair_top3_recall': [passed, len(frozen)], 'miss_reasons': dict(miss_reasons),
              'scope': 'CRS Identical links, exact selected latest versions; not a precision estimate',
              'outcomes': outcomes}
    write_json(result, output)
    print(json.dumps({k:v for k,v in result.items() if k != 'outcomes'},indent=2))


if __name__ == '__main__':
    main(Path(sys.argv[1]))
