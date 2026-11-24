"""Temporary review-branch-only transport of a fresh, fully audited bill snapshot."""
import json
import os
from pathlib import Path
import subprocess

BRANCH = 'overhaul/source-review-2026-11-16'
MAIN = 'd1e83aa7bf0a90aa135a5a8d8020e26a5606c0a4'
REVIEW = 'd23e6c8507359059e7e9b9643c0759fe75fe7421'
CORPUS = '98cf84df6a5b0b632fb25aeee6092a8594eaa0e569d842863042c692f3790c53'
STATUS = '2bd4f6aa1f59a09c4f2013c06c658c3b6eff90a5516a13e2f7d08a89cdd99a03'


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def remote(branch):
    return git('ls-remote', 'origin', 'refs/heads/' + branch).split()[0]


def read(path):
    return json.loads(Path(path).read_text())


assert os.environ['GITHUB_REPOSITORY'] == 'OpenSourcePatents/Congresswatch'
assert os.environ['GITHUB_REF'] == 'refs/heads/' + BRANCH
head = os.environ['GITHUB_SHA']
assert git('rev-parse', 'HEAD') == head and git('rev-parse', 'HEAD^') == REVIEW
assert remote(BRANCH) == head and remote('main') == MAIN
out = Path(os.environ['RUNNER_TEMP']) / 'reviewed-bill-refresh'
subprocess.run(['python', 'refresh_official_bills.py', '--expected-head', head, '--out', str(out)], check=True)
manifest = read(out / 'data/bill_refresh_manifest.json')
assert manifest['sources']['corpus_sha256'] == CORPUS, 'live corpus moved again; refuse publication'
assert manifest['sources']['status_sha256'] == STATUS, 'live metadata moved again; refuse publication'
assert manifest['sources']['errors'] == []
protected = ['votes', 'votes_updated', 'votes_checked', 'votes_fail_count', 'votes_status',
             'votes_refresh_provenance', 'trades', 'travel', 'top_donors_list',
             'edgar_cik', 'corporate_insider_signals', 'score', 'score_components', 'flags']
members = read('data/members.json')
for member in members:
    p = Path('data/details') / (member['id'] + '.json')
    old, new = read(p), read(out / p)
    assert {k:old.get(k) for k in protected} == {k:new.get(k) for k in protected}, member['id']
new_members = {m['id']:m for m in read(out / 'data/members.json')}
for member in members:
    for k in ['data_updated', 'roster_checked', 'roster_refresh_provenance', 'score', 'score_components', 'flags']:
        assert member.get(k) == new_members[member['id']].get(k), (member['id'], k)
assert remote(BRANCH) == head and remote('main') == MAIN
subprocess.run(['python', 'refresh_official_bills.py', '--expected-head', head, '--out', str(out), '--install'], check=True)
subprocess.run(['git', 'add', '--', 'data/details', 'data/members.json', 'data/stats.json',
                'data/trades.json', 'data/bills.json', 'data/bill_refresh_manifest.json',
                'data/bill_refresh_catalog.json'], check=True)
assert all(p.startswith('data/') for p in git('diff', '--cached', '--name-only').splitlines())
subprocess.run(['git', '-c', 'user.name=CongressWatch review refresh',
                '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
                'commit', '-m', 'Refresh newly published official bill text while preserving reconciled source attempts'], check=True)
commit = git('rev-parse', 'HEAD')
assert remote(BRANCH) == head and remote('main') == MAIN
subprocess.run(['git', 'push', 'origin', commit + ':refs/heads/' + BRANCH], check=True)
assert remote(BRANCH) == commit
print(json.dumps({'head':commit,'tree':git('rev-parse','HEAD^{tree}'),'main_still_current':remote('main')==MAIN,
                  'corpus_sha256':CORPUS,'status_sha256':STATUS,'protected_source_fields_preserved':True,
                  'records':manifest['records'],'matches':manifest['matches']}))
