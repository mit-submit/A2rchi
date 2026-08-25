"""Locate the payload the wheel carries.

`okg install --profile cern-team` needs a directory containing
`<profile>/profile.yaml`. Before this module that directory only existed
in an authoring checkout of this repo, so an install required cloning
archi even though the code arrived as a wheel. The wheel now carries the
bundles and playbooks (see `[tool.hatch.build.targets.wheel.force-include]`
in pyproject.toml), and these helpers point at them wherever pip put them:

    OKG_PROFILES_DIR="$(archi-profiles-dir)" okg install --profile cern-team

The env var is still required because okg's profile resolver has no
installed-distribution discovery — its cascade is explicit override,
`OKG_PROFILES_DIR`, `./profiles/`, the substrate's own library, then the
`okg fetch-deployments` cache (`profile_init.py`). Closing that last step
is the okg#1179 half of artifact-only install; shipping the payload is
ours, and it is what removes the checkout.
"""
from __future__ import annotations

import argparse
from pathlib import Path


def _package_root() -> Path:
    return Path(__file__).resolve().parent


def _resolve(name: str) -> Path:
    """The packaged copy, or the checkout's copy when running from source.

    An editable install has no `archi/bundles/` — the payload only lands there
    when the wheel is built. Falling back to the repo root keeps the helper
    honest in a development tree instead of returning a path that does not
    exist; the packaged copy always wins when present.
    """
    packaged = _package_root() / name
    if packaged.is_dir():
        return packaged
    checkout = _package_root().parents[1] / name
    if checkout.is_dir():
        return checkout
    return packaged


def profiles_dir() -> Path:
    """Directory to hand to `OKG_PROFILES_DIR` (contains `<bundle>/profile.yaml`)."""
    return _resolve("bundles")


def playbooks_dir() -> Path:
    """The distribution's playbooks, materialised in the wheel (never symlinks)."""
    return _resolve("skills")


def bundle_dir(name: str) -> Path:
    """One bundle by name, e.g. `bundle_dir("cern-team")`."""
    candidate = profiles_dir() / name
    if not (candidate / "profile.yaml").is_file():
        available = sorted(
            p.name for p in profiles_dir().iterdir() if (p / "profile.yaml").is_file()
        ) if profiles_dir().is_dir() else []
        raise FileNotFoundError(
            f"no bundle {name!r} in {profiles_dir()}; available: {available or 'none'}"
        )
    return candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="archi-profiles-dir",
        description="Print the installed bundle directory for OKG_PROFILES_DIR.",
    )
    parser.add_argument(
        "--playbooks",
        action="store_true",
        help="print the materialised playbooks directory instead",
    )
    parser.add_argument("--bundle", help="print one bundle's directory by name")
    args = parser.parse_args(argv)
    if args.bundle:
        print(bundle_dir(args.bundle))
    elif args.playbooks:
        print(playbooks_dir())
    else:
        print(profiles_dir())
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
