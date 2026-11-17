#!/usr/bin/env python3
"""Read-only audit of every published member and its real on-disk records.

Run before publishing: python audit_data.py. Empty arrays are reported as
coverage gaps, never treated as proof that no activity occurred.
"""
import json
import re
from collections import Counter
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent / 'data'
    members = json.loads((root / 'members.json').read_text())
    coverage, statuses = Counter(), Counter()
    errors, missing = [], {}
    ids = [m.get('id') or m.get('bioguide_id') for m in members]
    if len(set(ids)) != len(ids) or not all(ids):
        errors.append('Roster contains missing or duplicate member IDs')
    for member, bid in zip(members, ids):
        path = root / 'details' / f'{bid}.json'
        if not path.exists():
            errors.append(f'{bid}: missing detail file')
            continue
        detail = json.loads(path.read_text())
        coverage['detail_files'] += 1
        gaps = []
        for field in ('votes', 'bills', 'trades', 'travel', 'top_donors_list'):
            records = detail.get(field, [])
            if records is None:
                records = []
            if not isinstance(records, list):
                errors.append(f'{bid}: {field} is not an array')
                continue
            if any(not isinstance(record, dict) for record in records):
                errors.append(f'{bid}: {field} contains non-object records')
                continue
            if records:
                coverage[f'members_with_{field}'] += 1
            else:
                gaps.append(field)
            coverage[f'{field}_records'] += len(records)
        for bill in detail.get('bills') or []:
            if isinstance(bill, dict) and not re.fullmatch(r'(?:HR|HRES|HJRES|HCONRES|S|SRES|SJRES|SCONRES)\d+-\d+', str(bill.get('bill_id') or '')):
                coverage['malformed_bill_ids_preserved'] += 1
        coverage['unparsed_trade_filings'] += sum(bool(t.get('scanned_pdf')) for t in (detail.get('trades') or []) if isinstance(t, dict))
        coverage['trips_without_known_cost'] += sum(not t.get('total_cost') for t in (detail.get('travel') or []) if isinstance(t, dict))
        if detail.get('score_components'):
            expected = min(sum(detail['score_components'].values()), 100)
            if expected != detail.get('score') or detail.get('score') != member.get('score'):
                errors.append(f'{bid}: score/component/leaderboard mismatch')
        for field in ('votes_status', 'bills_status', 'donors_status'):
            if detail.get(field):
                statuses[f'{field}:{detail[field]}'] += 1
        if gaps:
            missing[bid] = gaps
    stats_path = root / 'stats.json'
    if stats_path.exists():
        stats = json.loads(stats_path.read_text())
        expected_signals = sum(m.get('corporate_insider_signals') or 0 for m in members)
        if stats.get('total_insider_signals') != expected_signals:
            errors.append('stats.json SEC total differs from current member snapshot')
    report = {'members': len(members), 'coverage': dict(coverage),
              'source_statuses': dict(statuses), 'errors': errors,
              'empty_sections_by_member': missing,
              'note': 'Empty sections indicate unavailable or empty stored coverage, not verified absence of activity.'}
    print(json.dumps(report, indent=2))
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
