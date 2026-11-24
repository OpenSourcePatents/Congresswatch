"""Fetch exact public API source blobs for isolated contract checks only."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

manifest = json.loads(Path("validation/api_reference_manifest.json").read_text())
root = Path(".cache/osp-api-reference")
for record in manifest["files"]:
    path = root/record["path"]
    if path.exists():
        data = path.read_bytes()
    else:
        url = f"https://raw.githubusercontent.com/{manifest['repository']}/{manifest['commit']}/{record['path']}"
        with urlopen(url, timeout=60) as response:
            data = response.read()
    blob = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
    if blob != record["git_blob_sha"]:
        raise ValueError("Immutable API blob mismatch: " + record["path"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
print("Verified", len(manifest["files"]), "immutable API source blobs; no API writes.")
