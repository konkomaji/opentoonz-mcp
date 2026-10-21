"""Release gate: one version everywhere, and a dated CHANGELOG section for it.

usage: python scripts/release_check.py [--tag vX.Y.Z] [--notes OUT_FILE]
Checks pyproject.toml, otmcp/__init__.py, server.json (top-level and package) and CHANGELOG.md.
With --notes, writes that version's changelog section (used as the GitHub release notes).
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag")
    ap.add_argument("--notes")
    a = ap.parse_args()
    errors = []
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")  # no tomllib on 3.10
    v_py = re.search(r'^\[project\][^\[]*?^version\s*=\s*"([^"]+)"', pyproject, re.M | re.S).group(1)
    v_init = re.search(r'__version__ = "([^"]+)"', (ROOT / "otmcp" / "__init__.py").read_text(encoding="utf-8")).group(1)
    sj = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    found = {"pyproject.toml": v_py, "otmcp/__init__.py": v_init, "server.json": sj["version"]}
    for p in sj.get("packages", []):
        found[f"server.json package {p['identifier']}"] = p["version"]
    if a.tag:
        found["git tag"] = a.tag.removeprefix("refs/tags/").removeprefix("v")
    if len(set(found.values())) != 1:
        errors.append("version mismatch: " + ", ".join(f"{k}={v}" for k, v in found.items()))
    log = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## \[{re.escape(v_py)}\] - (\d{{4}}-\d{{2}}-\d{{2}})\s*$(.*?)(?=^## \[|\Z)", log, re.M | re.S)
    if not m:
        errors.append(f"CHANGELOG.md has no '## [{v_py}] - YYYY-MM-DD' section")
    elif not m.group(2).strip():
        errors.append(f"CHANGELOG.md section {v_py} is empty")
    if "## [Unreleased]" not in log:
        errors.append("CHANGELOG.md must keep an '## [Unreleased]' section at the top")
    if errors:
        print("\n".join("ERROR: " + e for e in errors))
        return 1
    if a.notes:
        Path(a.notes).write_text(m.group(2).strip() + "\n", encoding="utf-8")
    print(f"release {v_py}: versions consistent, changelog dated {m.group(1)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
