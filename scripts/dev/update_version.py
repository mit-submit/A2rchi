#!/usr/bin/env python3
"""Update project version across metadata files."""

import argparse
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FILES_AND_PATTERNS = [
    (PROJECT_ROOT / "pyproject.toml", r"^version\s*=\s*\".*?\"", 'version = "{version}"'),
    (PROJECT_ROOT / "docs/mkdocs.yml", r"(^\s*version:\s*)[^\n]+", r"\1{version}"),
]

def update_file(path: Path, pattern: str, replacement_template: str, version: str) -> bool:
    text = path.read_text()
    replacement = replacement_template.format(version=version)
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count:
        path.write_text(new_text)
    return bool(count)

def main() -> None:
    parser = argparse.ArgumentParser(description="Update version strings across project files.")
    parser.add_argument("version", help="Version string to write (e.g. 1.2.3)")
    args = parser.parse_args()

    changed_any = False
    for path, pattern, template in FILES_AND_PATTERNS:
        if update_file(path, pattern, template, args.version):
            changed_any = True
        else:
            print(f"[WARN] No version entry updated in {path}")

    if not changed_any:
        raise SystemExit("No version strings were updated; aborting.")

if __name__ == "__main__":
    main()
