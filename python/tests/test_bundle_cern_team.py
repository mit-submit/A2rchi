"""cern-team bundle structural guards (W6; okg#1185 release-claim shape).

The install demo (docs/cern-team-demo.md) proves the bundle live; these
tests keep its structure honest offline: strict-admission blocks on
every connector default, resolvable playbook symlinks, no operator
paths or credential values in bundle files.
"""
import importlib.util
import re
from pathlib import Path

import yaml

BUNDLE = Path(__file__).resolve().parents[2] / "bundles" / "cern-team"


def test_profile_parses_with_required_questions():
    profile = yaml.safe_load((BUNDLE / "profile.yaml").read_text())
    assert profile["name"] == "cern-team"
    ids = {q["id"] for q in profile["init_questions"]}
    assert {"deployment_name", "postgres_dsn", "archi_data_root"} <= ids


def test_source_defaults_carry_strict_admission_shape():
    defaults = sorted((BUNDLE / "source-defaults").glob("*.yaml"))
    assert defaults, "no connector defaults in the bundle"
    for path in defaults:
        entries = yaml.safe_load(path.read_text())
        for name, entry in entries.items():
            policy = entry.get("admission_policy", {})
            assert policy.get("output_signature"), f"{path.name}:{name} missing output_signature"
            assert policy.get("output_scope_summary"), f"{path.name}:{name} missing output_scope_summary"
            assert entry.get("sync"), f"{path.name}:{name} missing sync block"
            # A bundle legitimately composes BOTH archi connectors and
            # substrate-resident okg sources. What must not appear is a
            # module from anywhere else: a bundle reaching outside these two
            # homes ships a dependency its consumers have no way to install.
            module = entry["module"]
            assert module.startswith(
                ("archi.sources.", "okg.substrate.library.sources.")
            ), f"{path.name}:{name} is neither an archi connector nor a substrate source"
            # A prefix check alone is weaker than what it replaced. `archi.*`
            # modules ship in this wheel, so a rename breaks the import tests
            # next door; `okg.*` modules do not — okg is the host environment
            # and pyproject declares no dependency on it, so nothing else here
            # would notice okg renaming or relocating a source. Resolve it for
            # real: an unimportable module is a bundle that fails at ingest
            # time on an operator's machine, which is the worst place to find
            # out.
            if module.startswith("okg."):
                assert importlib.util.find_spec(module) is not None, (
                    f"{path.name}:{name} names {module}, which does not resolve "
                    "in this environment — the host okg has renamed, moved or "
                    "dropped it"
                )


def test_playbook_symlinks_resolve():
    skills = BUNDLE / "skills"
    links = list(skills.iterdir())
    assert len(links) >= 20
    for link in links:
        assert link.resolve().exists(), f"dangling playbook symlink: {link}"


def test_no_operator_paths_or_secrets_in_bundle():
    pattern = re.compile(r"(/Users/|/root/|/work/submit/|/home/submit/|password\s*[:=]\s*\S|token\s*[:=]\s*[A-Za-z0-9]{16,})")
    for path in BUNDLE.rglob("*"):
        if path.is_file() and not path.is_symlink():
            for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                assert not pattern.search(line), f"{path}:{lineno}: {line.strip()[:80]}"
