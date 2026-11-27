"""Offline, pinned replay of reviewed SEC semantics; never fetches or publishes."""
import copy
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fetch_finance import save_json

MANIFEST = ROOT / 'validation/finance_run_46018133463.json'
SEC_FIELDS = ('edgar_cik', 'corporate_insider_signals', 'edgar_status',
              'edgar_updated', 'edgar_identity_status', 'edgar_data_status')


def original(rev, path):
    return json.loads(subprocess.check_output(['git', 'show', f'{rev}:{path}'], cwd=ROOT))


def classify(lines):
    # Any failed alias invalidates resolution even if another alias found a CIK.
    if any('SEC search failed' in line or 'EDGAR query failed' in line for line in lines):
        return 'query_failed'
    if any('EDGAR Hit:' in line for line in lines):
        return 'unverified_auto'
    for status in ('unresolved', 'needs_review'):
        if any(f'EDGAR: {status}' in line for line in lines):
            return status
    raise ValueError('Unclassifiable SEC attempt')


def reconcile_record(record, prior, trace):
    result = copy.deepcopy(record)
    assert prior.get('edgar_identity_status') != 'verified', 'Requires individual review of verified input'
    status = classify(trace['log_lines'])
    result['edgar_identity_status'] = 'unverified'
    result['edgar_status'] = status
    if status == 'query_failed':
        # Restore the complete prior detail pair, never mix the latest candidate
        # with retained counts. Do not invent a last-good retrieval timestamp.
        for key in ('edgar_cik', 'corporate_insider_signals', 'edgar_updated'):
            if key in prior:
                result[key] = copy.deepcopy(prior[key])
            elif key in result:
                # Preserve an explicit nullable field in legacy serialization.
                result[key] = None
        result['edgar_data_status'] = 'stale' if prior.get('edgar_cik') and prior.get('corporate_insider_signals') is not None else 'unavailable'
    elif status == 'unverified_auto':
        hits = [re.search(r'EDGAR Hit: (\d+) filings via CIK (\d+)', line)
                for line in trace['log_lines'] if 'EDGAR Hit:' in line]
        assert len(hits) == 1 and hits[0]
        count, cik = int(hits[0][1]), hits[0][2]
        assert result.get('edgar_cik') == cik and result.get('corporate_insider_signals') == count
        result['edgar_data_status'] = 'available'
    else:
        assert result.get('corporate_insider_signals') == 0
        result['edgar_cik'] = None
        result['edgar_data_status'] = 'unresolved'
    return result


def verified_manifest():
    manifest = json.loads(MANIFEST.read_text())
    traces = manifest['members']
    base, prior = manifest['main_sha'], manifest['prior_review_sha']
    roster = original(base, 'data/members.json')
    assert set(traces) == {m['id'] for m in roster} and len(traces) == 539
    for m in roster:
        bid = m['id']
        detail = original(base, f'data/details/{bid}.json')
        old = original(prior, f'data/details/{bid}.json')
        trace = traces[bid]
        for label, obj in [('incoming_roster', m), ('incoming_detail', detail), ('prior_detail', old)]:
            assert trace[label] == {k: obj[k] for k in SEC_FIELDS if k in obj}, (bid, label)
        assert trace['classification'] == classify(trace['log_lines'])
    lines = [line for t in traces.values() for line in t['log_lines']]
    assert sum('SEC search failed' in line and '500' in line for line in lines) == 124
    assert sum(t['classification'] == 'query_failed' for t in traces.values()) == 114
    return manifest


def main():
    manifest = verified_manifest()
    base, prior = manifest['main_sha'], manifest['prior_review_sha']
    members = original(base, 'data/members.json')
    for i, member in enumerate(members):
        bid = member['id']; path = f'data/details/{bid}.json'
        old = original(prior, path); trace = manifest['members'][bid]
        save_json(str(ROOT/path), reconcile_record(original(base, path), old, trace))
        members[i] = reconcile_record(member, old, trace)
    save_json(str(ROOT/'data/members.json'), members)
    print(json.dumps({'members': len(members), 'manifest_sha256': hashlib.sha256(MANIFEST.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
