#!/usr/bin/env python3
"""Update service Dockerfiles to reference a specific base image tag."""

import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES_DIR = PROJECT_ROOT / "src" / "cli" / "templates" / "dockerfiles"

def update_base_tags(args) -> None:

    for path in DOCKERFILES_DIR.glob("Dockerfile*"):
        print("Updating:", path)

        original = path.read_text()
        updated = original

        for base in args.bases:

            # update the tag if specified
            if args.tag:
                if base == "python":
                    PYTHON_BASE = "a2rchi-python-base"
                    updated = updated.replace(f"{PYTHON_BASE}:{args.orig_tag}", f"{PYTHON_BASE}:{args.tag}")
                elif base == "pytorch":
                    PYTORCH_BASE = "a2rchi-pytorch-base"
                    updated = updated.replace(f"{PYTORCH_BASE}:{args.orig_tag}", f"{PYTORCH_BASE}:{args.tag}")

            # switch source if specified
            PYTHON_BASE_DH = "docker.io/a2rchi/"
            PYTHON_BASE_LOCAL = "localhost/a2rchi/"
            if args.switch_source == "localhost":
                print("Switching python base to localhost")
                updated = updated.replace(PYTHON_BASE_DH, PYTHON_BASE_LOCAL)
            elif args.switch_source == "dockerhub":
                print("Switching python base to dockerhub")
                updated = updated.replace(PYTHON_BASE_LOCAL, PYTHON_BASE_DH)
        
        if updated != original:
            path.write_text(updated)
            rel_path = path.relative_to(PROJECT_ROOT)

def main() -> None:
    parser = argparse.ArgumentParser(description="Point service Dockerfiles at the given base image tag.")
    parser.add_argument("--tag", help="Base image tag to reference, e.g. v1.2.3", default=None)
    parser.add_argument("--orig-tag", help="Original tag to replace, e.g. latest", default="latest")
    parser.add_argument("--switch-source", choices=["dockerhub", "localhost"], help="Switch source for base images.", default=None)
    parser.add_argument("--bases", choices=["python", "pytorch"], nargs="+", default=["python", "pytorch"], help="Base images to update.")
    args = parser.parse_args()
    update_base_tags(args)


if __name__ == "__main__":
    main()
