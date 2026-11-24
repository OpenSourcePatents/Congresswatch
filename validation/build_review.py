"""Check the static site's actual JS, Python syntax and JSON payloads."""
import ast
import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    python_files = [p for p in ROOT.rglob("*.py") if not any(
        x.startswith(".") for x in p.relative_to(ROOT).parts)]
    for path in python_files:
        ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    scripts = re.findall(r"<script(?:\s[^>]*)?>([\s\S]*?)</script>", (ROOT/"index.html").read_text())
    with tempfile.TemporaryDirectory() as temp:
        for i, code in enumerate(scripts):
            path = Path(temp)/f"inline-{i}.js"
            path.write_text(code)
            subprocess.run(["node", "--check", str(path)], check=True)
    count = 0
    for path in (ROOT/"data").rglob("*.json"):
        json.loads(path.read_text())
        count += 1
    print(json.dumps({"static_build": "passed", "python_files": len(python_files),
                      "inline_scripts": len(scripts), "json_files": count}))


if __name__ == "__main__":
    main()
