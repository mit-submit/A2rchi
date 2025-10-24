#!/usr/bin/env python3
"""Update project version across metadata files."""

import argparse
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

FILES_AND_PATTERNS = [
    (
        PROJECT_ROOT / "pyproject.toml",
        re.compile(r'^version\s*=\s*".*?"', re.MULTILINE),
        lambda match, version: f'version = "{version}"',
    ),
    (
        PROJECT_ROOT / "docs/mkdocs.yml",
        re.compile(r'(^\s*version:\s*)[^\n]+', re.MULTILINE),
        lambda match, version: f"{match.group(1)}{version}",
    ),
]


def update_file(path: Path, pattern: re.Pattern[str], formatter, version: str) -> bool:
    text = path.read_text()
    new_text, count = pattern.subn(lambda match: formatter(match, version), text, count=1)
    if count:
        path.write_text(new_text)
    return bool(count)


def main() -> None:
    parser = argparse.ArgumentParser(description="Update version strings across project files.")
    parser.add_argument("version", help="Version string to write (e.g. 1.2.3)")
    args = parser.parse_args()

    changed_any = False
    for path, pattern, formatter in FILES_AND_PATTERNS:
        if update_file(path, pattern, formatter, args.version):
            changed_any = True
            print(f"[OK] Updated version in {path}")
        else:
            print(f"[WARN] No version entry updated in {path}")

    if not changed_any:
        raise SystemExit("No version strings were updated; aborting.")


if __name__ == "__main__":
    main()
