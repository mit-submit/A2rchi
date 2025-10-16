#!/usr/bin/env python3
"""Repoint service Dockerfiles to use freshly built local base images."""

import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES_DIR = PROJECT_ROOT / "src" / "cli" / "templates" / "dockerfiles"

PYTHON_BASE = "docker.io/a2rchi/a2rchi-python-base"
PYTORCH_BASE = "docker.io/a2rchi/a2rchi-pytorch-base"


def rewrite_dockerfiles(tag: str, reverse: bool) -> None:
    replacements = {
        f"{PYTHON_BASE}:latest": f"a2rchi/a2rchi-python-base:{tag}",
        f"{PYTHON_BASE}:{tag}": f"a2rchi/a2rchi-python-base:{tag}",
        f"{PYTORCH_BASE}:latest": f"a2rchi/a2rchi-pytorch-base:{tag}",
        f"{PYTORCH_BASE}:{tag}": f"a2rchi/a2rchi-pytorch-base:{tag}",
    }

    for path in DOCKERFILES_DIR.glob("Dockerfile*"):
        text = path.read_text()
        new_text = text
        for old, new in replacements.items():
            if reverse:
                old, new = new, old
            new_text = new_text.replace(old, new)
        if new_text != text:
            path.write_text(new_text)
            print(f"[local-base] Updated {path.relative_to(PROJECT_ROOT)}:")
            print("-----------")
            print(new_text)
            print("-----------")


def main() -> None:
    parser = argparse.ArgumentParser(description="Point Dockerfiles to local base images")
    parser.add_argument("tag", help="Tag of the freshly built base images")
    parser.add_argument("--reverse", action="store_true", help="Revert to using remote base images")
    args = parser.parse_args()
    rewrite_dockerfiles(args.tag, args.reverse)



if __name__ == "__main__":
    main()
