#!/usr/bin/env python3
"""Update service Dockerfiles to reference a specific base image tag."""

import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES_DIR = PROJECT_ROOT / "src" / "cli" / "templates" / "dockerfiles"

PYTHON_BASE = "docker.io/a2rchi/a2rchi-python-base"
PYTORCH_BASE = "docker.io/a2rchi/a2rchi-pytorch-base"


def update_base_tags(tag: str) -> None:
    replacements = {
        f"{PYTHON_BASE}:latest": f"a2rchi/a2rchi-python-base:{tag}",
        f"a2rchi/a2rchi-python-base:latest": f"a2rchi/a2rchi-python-base:{tag}",
        f"{PYTORCH_BASE}:latest": f"a2rchi/a2rchi-pytorch-base:{tag}",
        f"a2rchi/a2rchi-pytorch-base:latest": f"a2rchi/a2rchi-pytorch-base:{tag}",
    }

    for path in DOCKERFILES_DIR.glob("Dockerfile*"):
        original = path.read_text()
        updated = original
        for old, new in replacements.items():
            updated = updated.replace(old, new)
        if updated != original:
            path.write_text(updated)
            rel_path = path.relative_to(PROJECT_ROOT)
            print(f"[base-tag] Updated {rel_path} to use tag {tag}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Point service Dockerfiles at the given base image tag.")
    parser.add_argument("tag", help="Base image tag to reference, e.g. v1.2.3")
    args = parser.parse_args()
    update_base_tags(args.tag)


if __name__ == "__main__":
    main()
