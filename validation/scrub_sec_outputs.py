"""Offline SEC-only remediation. Preserve raw inputs and other score components."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch_finance import compute_score_components, load_json, save_json
from sec_identity import eligible_sec_signals


def scrub_record(record, detail):
    """Use the detail's complete SEC record and retain every non-SEC component."""
    updated = dict(record)
    components = dict(detail['score_components'])
    if set(components) != {'trade_timing', 'wealth_gap', 'donor_alignment',
                           'alec_similarity', 'foreign_travel', 'attendance'}:
        raise ValueError('Incomplete score components; refusing a partial repair')
    if (detail.get('corporate_insider_signals') or 0) > 0 or 'trade' in detail.get('flags', []):
        components['trade_timing'] = compute_score_components(detail, detail)['trade_timing']
    updated['score_components'] = components
    updated['score'] = min(sum(components.values()), 100)
    updated['flags'] = [f for f in record.get('flags', []) if f != 'trade']
    if eligible_sec_signals(detail) > 5:
        updated['flags'].insert(0, 'trade')
    updated['sec_attribution_version'] = 1
    return updated


def main(root=Path('data')):
    members = load_json(str(root/'members.json'), [])
    assert members, 'Missing roster'
    changes = []
    for i, member in enumerate(members):
        bid = member.get('id') or member.get('bioguide_id')
        path = root/'details'/f'{bid}.json'
        detail = load_json(str(path), {})
        if not detail:
            raise ValueError(f'Missing detail {bid}')
        revised = scrub_record(detail, detail)
        light = scrub_record(member, detail)
        # Do not copy a CIK or count independently across conflicting raw records.
        if revised != detail:
            save_json(str(path), revised)
        members[i] = light
        if detail.get('score') != revised['score'] or detail.get('flags') != revised['flags']:
            changes.append({'id': bid, 'name': member['name'],
                            'score_before': detail.get('score'), 'score_after': revised['score'],
                            'trade_before': detail['score_components']['trade_timing'],
                            'trade_after': revised['score_components']['trade_timing'],
                            'raw_cik': detail.get('edgar_cik'),
                            'raw_count': detail.get('corporate_insider_signals')})
    save_json(str(root/'members.json'), members)
    stats = load_json(str(root/'stats.json'), {})
    stats.update(total_members=len(members),
                 members_with_scores=sum(m['score'] > 0 for m in members),
                 high_anomaly=sum(m['score'] >= 60 for m in members),
                 total_insider_signals=sum(eligible_sec_signals(m) for m in members))
    save_json(str(root/'stats.json'), stats)
    print(json.dumps({'members': len(members), 'changed_scores_or_flags': changes,
                      'eligible_sec_total': stats['total_insider_signals']}, indent=2))


if __name__ == '__main__':
    main()
