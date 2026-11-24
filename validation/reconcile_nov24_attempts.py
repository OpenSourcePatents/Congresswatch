"""Pinned, idempotent reconciliation of timestamp-only main commits.

No collector runs. Every incoming field is checked before any output is written.
A different source delta needs a separate review, never a best-effort merge.
"""
import copy
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import subprocess

BASE = '3d3ee672e4e1fd7669ed6850f36e3c7e94f3bba0'
REVIEW = '7542279dbcf40cfb299f20482a8d196f9d0f3c07'
MAIN = 'd1e83aa7bf0a90aa135a5a8d8020e26a5606c0a4'
ROSTER = '41c72c8f17aae4eb2fcd79022708c0ea69743a9d'
VOTE_RUN = 45387356496


def git(*args):
    return subprocess.check_output(['git', *args])


@lru_cache(maxsize=4096)
def read(ref, path):
    return json.loads(git('show', ref + ':' + path))


def changed(a, b):
    return {k for k in a.keys() | b.keys() if a.get(k) != b.get(k)}


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=True) + '\n').encode()


def main():
    rosters = [read(ref, 'data/members.json') for ref in (BASE, REVIEW, MAIN)]
    maps = [{m['id']: m for m in roster} for roster in rosters]
    assert all(len(m) == 539 for m in maps)
    assert maps[0].keys() == maps[1].keys() == maps[2].keys()
    paths = set(git('diff', '--name-only', BASE, MAIN).decode().splitlines())
    assert paths == {'data/members.json'} | {f'data/details/{bid}.json' for bid in maps[0]}
    outputs, records, members = {}, [], []
    for old_member in rosters[1]:
        bid = old_member['id']
        base_member, incoming_member = maps[0][bid], maps[2][bid]
        assert changed(base_member, incoming_member) == {'data_updated'}, bid
        member = copy.deepcopy(old_member)
        member['roster_checked'] = incoming_member['data_updated']
        member['roster_refresh_provenance'] = {
            'source_snapshot': ROSTER,
            'legacy_data_updated': incoming_member['data_updated'],
            'substantive_fields_changed': False,
            'note': 'Timestamp-only roster check; not a fresh finance, SEC or vote collection.'}
        assert member.get('data_updated') == old_member.get('data_updated')
        members.append(member)
        path = f'data/details/{bid}.json'
        base, old, incoming = [read(ref, path) for ref in (BASE, REVIEW, MAIN)]
        assert changed(base, incoming) == {'votes_updated', 'votes_fail_count'}, bid
        assert incoming.get('votes_status') == base.get('votes_status') == 'fetch_failed'
        assert incoming.get('votes') == base.get('votes'), bid
        assert incoming['votes_fail_count'] == base['votes_fail_count'] + 1, bid
        assert old['votes_fail_count'] == base['votes_fail_count'], bid
        assert incoming['votes_updated'] > old['votes_checked'], bid
        result = copy.deepcopy(old)
        # Assign the observed counter; never increment on reconciliation/replay.
        result['votes_fail_count'] = incoming['votes_fail_count']
        result['votes_checked'] = incoming['votes_updated']
        provenance = result.setdefault('votes_refresh_provenance', {})
        previous = provenance.get('latest_attempt')
        if previous:
            history = provenance.setdefault('prior_attempts', [])
            if previous not in history:
                history.append(copy.deepcopy(previous))
        provenance['latest_attempt'] = {
            'source_snapshot': MAIN, 'prior_upstream_snapshot': BASE,
            'run': VOTE_RUN, 'checked': incoming['votes_updated'],
            'legacy_votes_updated': incoming['votes_updated'],
            'failed_attempt_count': incoming['votes_fail_count'],
            'status': 'fetch_failed',
            'note': 'Publisher failures; zero successful fetches. Preserve cached votes and last-good collection time.'}
        assert result.get('votes_updated') == old.get('votes_updated'), bid
        assert result.get('votes') == old.get('votes'), bid
        assert changed(old, result) == {'votes_fail_count', 'votes_checked', 'votes_refresh_provenance'}, bid
        outputs[path] = result
        records.append({'id': bid, 'votes_checked': result['votes_checked'],
                        'votes_fail_count': result['votes_fail_count'],
                        'votes_updated_preserved': result.get('votes_updated')})
    outputs['data/members.json'] = members
    # Refuse to overwrite unrelated local work; either the reviewed input or the
    # exact already-reconciled output is permitted. This also proves replay safety.
    for path, value in outputs.items():
        current = json.loads(Path(path).read_bytes())
        assert current in (read(REVIEW, path), value), ('unexpected local change', path)
    report = {'prior_base': BASE, 'review_input': REVIEW, 'main': MAIN,
              'roster_commit': ROSTER, 'vote_run': VOTE_RUN,
              'incoming_paths': len(paths), 'roster_timestamp_rows': len(members),
              'vote_attempt_rows': len(records), 'successful_vote_fetches': 0,
              'failed_vote_fetches': 539, 'substantive_source_changes': 0,
              'last_good_vote_timestamps_preserved': True,
              'counter_policy': 'Assign immutable observed counter; never increment on replay.',
              'attempts': records}
    outputs['benchmarks/nov24-attempt-reconciliation.json'] = report
    before = {p: Path(p).read_bytes() if Path(p).exists() else None for p in outputs}
    for path, value in outputs.items():
        Path(path).write_bytes(encoded(value))
    digest = hashlib.sha256(json.dumps({p: hashlib.sha256(encoded(v)).hexdigest()
                                       for p, v in outputs.items()}, sort_keys=True).encode()).hexdigest()
    print(json.dumps({'main': MAIN, 'output_files': len(outputs),
                      'changed_files': sum(before[p] != encoded(v) for p, v in outputs.items()),
                      'output_manifest_sha256': digest}))


if __name__ == '__main__':
    main()
