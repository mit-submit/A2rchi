"""task.w2 / W4 skills — the ported skill bundle is de-site-ified and wired.

Checks the top-level ``skills/`` directory (ADR 0001 target tree):
every skill file is nonempty, contains no instance-specific site markers
left over from the okg-deployments CMS deployment (main@f33a9c4), and the
shipped ``skill-triggers.yaml`` only references skills that exist.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "skills"

# Instance/site markers that must never appear in a ported skill:
# hostnames, operator paths, and the exact instance-specific phrases
# removed during de-site-ification.
SITE_MARKERS = [
    "submit76",
    "vocms",
    "/data/cmsai",
    "/work/submit",
    "/home/submit",
    "lxplus",
    ".mit.edu",
    # de-site-ified in okg_retrieval_planner.md (instance capability claim)
    "For this CMS deployment",
    # de-site-ified in okg_traversal.md (provider-specific agent name)
    "when Codex needs",
]

# The 18 skills ported from okg-deployments cms/skills at main@f33a9c4.
PORTED_SKILLS = [
    "agentic_benchmark",
    "answer_synthesis",
    "date_lookup",
    "evidence_compiler",
    "evidence_retrieval",
    "incident_root_cause",
    "migration_summary",
    "monitoring_condor_snapshot",
    "okg_retrieval_planner",
    "okg_traversal",
    "operator_knowledge",
    "policy_exception_arbitration",
    "procedure_extraction",
    "rucio_command",
    "rucio_dataset_hosting",
    "sealed_benchmark",
    "site_profile",
    "source_document_exploration",
]


def _skill_files():
    files = sorted(SKILLS_DIR.glob("*.md"))
    assert files, f"no skill files found in {SKILLS_DIR}"
    return files


def test_ported_skill_set_present():
    names = {p.stem for p in _skill_files()}
    missing = set(PORTED_SKILLS) - names
    assert not missing, f"ported skills missing from skills/: {sorted(missing)}"


def test_skills_nonempty():
    for path in _skill_files():
        text = path.read_text(encoding="utf-8").strip()
        assert text, f"{path} is empty"


def test_skills_have_no_site_markers():
    hits = []
    for path in _skill_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            for marker in SITE_MARKERS:
                if marker in line:
                    hits.append(f"{path}:{lineno}: {marker!r}: {line.strip()}")
    assert not hits, "site markers left in skills:\n" + "\n".join(hits)


def test_skill_triggers_reference_existing_skills():
    triggers_path = SKILLS_DIR / "skill-triggers.yaml"
    assert triggers_path.exists(), triggers_path
    triggers = yaml.safe_load(triggers_path.read_text(encoding="utf-8"))
    intents = triggers.get("intents")
    assert intents, "skill-triggers.yaml has no intents"
    for intent, spec in intents.items():
        skill = spec.get("skill")
        assert skill, f"intent {intent!r} names no skill"
        skill_file = SKILLS_DIR / f"{skill}.md"
        assert skill_file.exists(), (
            f"intent {intent!r} references missing skill file {skill_file}"
        )


def test_skill_triggers_has_no_site_markers():
    text = (SKILLS_DIR / "skill-triggers.yaml").read_text(encoding="utf-8")
    for marker in SITE_MARKERS:
        assert marker not in text, f"site marker {marker!r} in skill-triggers.yaml"
