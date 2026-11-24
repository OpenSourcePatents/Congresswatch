"""One-time reviewed Git history import; only the named review branch may move."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.request import Request, urlopen

manifest = json.loads(Path(".github/review-import.json").read_text())
branch = "overhaul/source-review-2026-11-16"
repo = "OpenSourcePatents/Congresswatch"
assert manifest["repository"] == os.environ["GITHUB_REPOSITORY"] == repo
assert manifest["branch"] == branch
assert os.environ["GITHUB_REF"] == "refs/heads/" + branch
bootstrap = os.environ["GITHUB_SHA"]


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def remote(ref):
    result = git("ls-remote", "origin", "refs/heads/" + ref).split()
    assert len(result) == 2
    return result[0]


assert remote(branch) == bootstrap, "review branch moved; refusing import"
assert remote("main") == manifest["main"], "main moved; reconcile before import"
bundle = Path(os.environ["RUNNER_TEMP"]) / "reviewed.bundle"
total = hashlib.sha256()
size = 0
with bundle.open("wb") as out:
    for index, part in enumerate(manifest["parts"]):
        assert part["index"] == index
        request = Request(f"https://api.github.com/repos/{repo}/git/blobs/{part['sha']}",
            headers={"Authorization": "Bearer " + os.environ["GH_TOKEN"],
                     "Accept": "application/vnd.github+json"})
        with urlopen(request, timeout=60) as response:
            payload = json.load(response)
        assert payload["encoding"] == "base64" and payload["sha"] == part["sha"]
        data = base64.b64decode(payload["content"])
        assert len(data) == part["size"]
        assert hashlib.sha256(data).hexdigest() == part["sha256"]
        assert hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == part["sha"]
        out.write(data)
        total.update(data)
        size += len(data)
assert size == manifest["size"] and total.hexdigest() == manifest["sha256"]
subprocess.run(["git", "fetch", "origin", *manifest["prerequisites"]], check=True)
subprocess.run(["git", "bundle", "verify", str(bundle)], check=True)
subprocess.run(["git", "fetch", str(bundle), "HEAD"], check=True)
assert git("rev-parse", "FETCH_HEAD") == manifest["commit"]
assert git("rev-parse", manifest["commit"] + "^{tree}") == manifest["tree"]
for ancestor in (manifest["old_review"], manifest["main"]):
    subprocess.run(["git", "merge-base", "--is-ancestor", ancestor, manifest["commit"]], check=True)
message = ("Import audited Congresswatch review history\n\n"
           + "Recovered commit: " + manifest["commit"] + "\n"
           + "Exact tested tree: " + manifest["tree"] + "\n"
           + "No main update, merge approval, deployment, or API repository write.\n")
commit = subprocess.check_output([
    "git", "-c", "user.name=Congresswatch review import",
    "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
    "commit-tree", manifest["tree"], "-p", bootstrap, "-p", manifest["commit"]],
    input=message, text=True).strip()
assert remote(branch) == bootstrap, "review branch moved; refusing push"
# Normal fast-forward only. The new commit descends from both the bootstrap
# and the original recovered history; this can never update main.
subprocess.run(["git", "push", "origin", commit + ":refs/heads/" + branch], check=True)
assert remote(branch) == commit
summary = {"review_head": commit, "tested_tree": manifest["tree"],
           "recovered_commit": manifest["commit"], "main_unchanged": remote("main") == manifest["main"]}
print(json.dumps(summary))
with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
    f.write("Imported exact audited tree to review branch only.\n\n" + json.dumps(summary))
