"""Verify current outputs and two independent deterministic regenerations."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def normalized(value):
    if isinstance(value, list):
        return [normalized(v) for v in value]
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items() if k not in ("score_updated", "last_updated")}
    return value


def hashes(root):
    paths = sorted((root/"data/details").glob("*.json"))
    paths += [root/"data"/name for name in ("members.json", "stats.json", "bills.json", "trades.json")]
    return {str(p.relative_to(root)): hashlib.sha256(json.dumps(
        normalized(json.loads(p.read_text())), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        for p in paths}


def main():
    original = hashes(ROOT)
    with tempfile.TemporaryDirectory() as temp:
        scratch = Path(temp)
        shutil.copytree(ROOT/"data", scratch/"data")
        for iteration in (1, 2):
            for script in ("recompute_scores.py", "build_aggregates.py"):
                subprocess.run([sys.executable, str(ROOT/script)], cwd=scratch, check=True,
                               stdout=subprocess.DEVNULL)
            current = hashes(scratch)
            changed = [p for p in original if current[p] != original[p]]
            if changed:
                raise AssertionError(f"generation {iteration} differs: {changed}")
        shutil.copyfile(ROOT/"audit_data.py", scratch/"audit_data.py")
        env = dict(os.environ, PYTHONPATH=str(ROOT))
        audit = subprocess.check_output([sys.executable, str(scratch/"audit_data.py")], cwd=scratch, env=env)
        assert not json.loads(audit)["errors"]
    digest = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
    print(json.dumps({"documents": len(original), "two_regenerations_identical": True,
                      "excluded_volatile_fields": ["score_updated", "last_updated"],
                      "normalized_manifest_sha256": digest}))


if __name__ == "__main__":
    main()
