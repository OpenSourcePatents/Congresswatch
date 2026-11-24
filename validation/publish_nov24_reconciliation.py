"""Temporary transport of the locally checked merge tree; review branch only."""
import json
import os
import subprocess
from pathlib import Path

REPO = 'OpenSourcePatents/Congresswatch'
BRANCH = 'overhaul/source-review-2026-11-16'
MAIN = 'd1e83aa7bf0a90aa135a5a8d8020e26a5606c0a4'
REVIEW = '7542279dbcf40cfb299f20482a8d196f9d0f3c07'
EXPECTED_DATA_TREE = 'f502fac98853dee1b6dbd4bc4ad52e4f43642287'


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def remote(branch):
    return git('ls-remote', 'origin', 'refs/heads/' + branch).split()[0]


assert os.environ['GITHUB_REPOSITORY'] == REPO
assert os.environ['GITHUB_REF'] == 'refs/heads/' + BRANCH
bootstrap = os.environ['GITHUB_SHA']
assert git('rev-parse', 'HEAD') == bootstrap
assert git('rev-parse', 'HEAD^') == REVIEW
assert remote(BRANCH) == bootstrap and remote('main') == MAIN
subprocess.run(['python', 'validation/reconcile_nov24_attempts.py'], check=True)
second = json.loads(subprocess.check_output(['python', 'validation/reconcile_nov24_attempts.py'], text=True))
assert second['changed_files'] == 0
subprocess.run(['git', 'add', '--', 'data/members.json', 'data/details',
                'benchmarks/nov24-attempt-reconciliation.json'], check=True)
assert git('rev-parse', git('write-tree') + ':data') == EXPECTED_DATA_TREE
changed = git('diff', '--cached', '--name-only').splitlines()
assert len(changed) == 541
assert all(p == 'data/members.json' or p.startswith('data/details/')
           or p == 'benchmarks/nov24-attempt-reconciliation.json' for p in changed)
tree = git('write-tree')
commit = subprocess.check_output(['git', '-c', 'user.name=CongressWatch review reconciliation',
    '-c', 'user.email=41898282+github-actions[bot]@users.noreply.github.com',
    'commit-tree', tree, '-p', bootstrap, '-p', MAIN],
    input='Reconcile main roster checks and failed vote attempts without refreshing cached evidence\n', text=True).strip()
assert remote(BRANCH) == bootstrap and remote('main') == MAIN
# Normal fast-forward to the existing review branch; no force or other target.
subprocess.run(['git', 'push', 'origin', commit + ':refs/heads/' + BRANCH], check=True)
assert remote(BRANCH) == commit
result = {'head': commit, 'tree': tree, 'main': MAIN, 'data_tree': EXPECTED_DATA_TREE,
          'main_still_current': remote('main') == MAIN, 'idempotent': True}
print(json.dumps(result))
Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(json.dumps(result, indent=2))
