"""The wheel must be installable as an artifact, not as a checkout.

`okg install --profile cern-team` needs `<profiles-dir>/cern-team/profile.yaml`
plus the bundle's playbooks. Before the force-include those existed only in an
authoring checkout of this repo. These tests assert the *built wheel* carries
them — `test_bundle_cern_team.py` checks the working tree, which is exactly the
thing that stayed green while the artifact was incomplete.
"""
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_PREFIX = "archi/bundles/cern-team/"


@pytest.fixture(scope="module")
def wheel_names(tmp_path_factory) -> list[str]:
    out = tmp_path_factory.mktemp("wheel")
    result = subprocess.run(
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(out)],
        cwd=REPO_ROOT / "python",
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-800:]
    wheels = sorted(out.glob("archi-3.*.whl"))
    assert wheels, "no wheel produced"
    with zipfile.ZipFile(wheels[-1]) as zf:
        return zf.namelist(), str(wheels[-1])


def test_wheel_ships_the_bundle_profile(wheel_names):
    names, _ = wheel_names
    assert f"{BUNDLE_PREFIX}profile.yaml" in names
    assert f"{BUNDLE_PREFIX}deployment-defaults.yaml" in names
    assert any(n.startswith(f"{BUNDLE_PREFIX}source-defaults/") for n in names)


def test_wheel_ships_playbooks_dereferenced(wheel_names):
    """The bundle's playbooks are symlinks in the repo; a wheel cannot carry a
    link usefully, so they must arrive as real content."""
    names, wheel = wheel_names
    playbooks = [
        n
        for n in names
        if n.startswith(f"{BUNDLE_PREFIX}skills/")
        and n.endswith(".md")
        and "README" not in n
    ]
    assert len(playbooks) >= 15, playbooks
    with zipfile.ZipFile(wheel) as zf:
        for name in playbooks:
            body = zf.read(name).decode("utf-8")
            # an unresolved symlink would serialize as a short relative path
            assert len(body) > 200, f"{name} looks like an unresolved link: {body!r}"
            assert body.lstrip().startswith("#"), f"{name} is not a playbook body"


def test_wheel_ships_the_distribution_playbooks(wheel_names):
    names, _ = wheel_names
    assert sum(1 for n in names if n.startswith("archi/skills/") and n.endswith(".md")) >= 15


def test_paths_helpers_resolve_to_a_real_directory():
    """Packaged copy when installed, checkout copy when running from source --
    either way the caller gets a directory that exists, never a dangling path."""
    from archi import paths

    assert paths.profiles_dir().name == "bundles"
    assert paths.playbooks_dir().name == "skills"
    assert paths.profiles_dir().is_dir()
    assert (paths.profiles_dir() / "cern-team" / "profile.yaml").is_file()
    assert paths.playbooks_dir().is_dir()


def test_bundle_dir_reports_available_bundles_when_missing():
    from archi import paths

    with pytest.raises(FileNotFoundError) as excinfo:
        paths.bundle_dir("no-such-bundle")
    assert "no-such-bundle" in str(excinfo.value)
    assert "available" in str(excinfo.value)


def test_wheel_carries_the_bundle_schema_slices(wheel_names):
    """The `schemas:` slot is only real if the ARTIFACT carries the files.

    The one-command install was first proven with OKG_PROFILES_DIR pointing at
    a source tree, which proves nothing about an installed wheel — the whole
    point of okg#1367 for an external consumer is that a pip install is enough.
    force-include maps all of ../bundles, so this passes today; it exists so a
    narrowed include cannot silently drop the schemas and leave the artifact
    unable to complete an install.
    """
    names, _ = wheel_names
    expected = {
        "archi/bundles/cern-team/schemas/operations.yaml",
        "archi/bundles/cern-team/schemas/sources.yaml",
        "archi/bundles/cern-team/schemas/bridges/operations.yaml",
        "archi/bundles/cern-team/schemas/bridges/sources.yaml",
    }
    assert expected <= set(names), f"wheel is missing: {sorted(expected - set(names))}"
