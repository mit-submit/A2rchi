import json
import threading
import uuid

import pytest

from src.evaluation.qa.artifacts import artifact_hashes, write_json
from src.evaluation.qa.console import EvaluationConsoleService
from src.evaluation.qa.history import EvaluationHistory
from src.evaluation.qa.jobs import EvaluationJobManager, JobConflictError


def test_console_job_exposes_current_atom_draft_status(tmp_path):
    service = EvaluationConsoleService(
        tmp_path,
        agent_config_path=tmp_path / "config.yaml",
        agents_dir=tmp_path,
    )
    draft_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    draft_path = service.catalog.drafts_dir / draft_id / "draft.json"
    write_json(draft_path, {"id": draft_id, "status": "open"})
    write_json(
        service.catalog.jobs_dir / f"{job_id}.json",
        {
            "id": job_id,
            "kind": "generate_atoms",
            "status": "completed",
            "context": {"dataset_id": str(uuid.uuid4())},
            "result": {"draft_id": draft_id},
        },
    )

    assert service.get_job(job_id)["result"]["draft_status"] == "open"
    write_json(draft_path, {"id": draft_id, "status": "saved"})
    assert service.get_job(job_id)["result"]["draft_status"] == "saved"
    service.jobs.close()


def test_job_manager_enforces_single_flight_and_persists_result(tmp_path):
    manager = EvaluationJobManager(tmp_path)
    release = threading.Event()

    job = manager.start(
        "generate_atoms",
        lambda: (release.wait(2), {"draft_id": "draft"})[1],
    )

    with pytest.raises(JobConflictError, match="already"):
        manager.start("evaluation", lambda: {})
    release.set()
    completed = manager.wait(job["id"], timeout=2)

    assert completed["status"] == "completed"
    assert completed["result"] == {"draft_id": "draft"}
    manager.close()


def test_job_manager_marks_stale_work_interrupted(tmp_path):
    job_id = "040bb55f-739c-46a8-a297-f49f54d1e759"
    write_json(
        tmp_path / f"{job_id}.json",
        {"id": job_id, "kind": "evaluation", "status": "running"},
    )

    manager = EvaluationJobManager(tmp_path)

    assert manager.get(job_id)["status"] == "interrupted"
    manager.close()


def test_history_lists_valid_runs_and_isolates_invalid_ones(tmp_path):
    valid = tmp_path / "valid"
    valid.mkdir()
    write_json(
        valid / "summary.json",
        {"overall_attempt_pass_rate": 0.75, "items": []},
    )
    (valid / "report.md").write_text("# Report\n")
    write_json(
        valid / "manifest.json",
        {
            "schema_version": "qa-v0",
            "run_id": "run-1",
            "status": "scored",
            "attempts": 2,
            "artifacts": artifact_hashes(valid, {"summary.json", "report.md"}),
            "phases": {
                "prepare": {"status": "completed"},
                "run": {"status": "completed"},
                "score": {
                    "status": "completed",
                    "completed_at": "2026-07-24T10:00:00+00:00",
                },
            },
        },
    )
    invalid = tmp_path / "invalid"
    invalid.mkdir()
    (invalid / "manifest.json").write_text(json.dumps({"schema_version": "qa-v99"}))
    history = EvaluationHistory(tmp_path)

    rows = history.list_runs()

    assert len(rows) == 2
    valid_row = next(row for row in rows if row["valid"])
    invalid_row = next(row for row in rows if not row["valid"])
    assert valid_row["overall_attempt_pass_rate"] == 0.75
    assert history.get_report(valid_row["id"]) == "# Report\n"
    assert "unsupported run schema" in invalid_row["error"]


def test_history_rejects_tampered_declared_artifacts(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    write_json(run / "summary.json", {"overall_attempt_pass_rate": 1.0})
    (run / "report.md").write_text("# Report\n")
    write_json(
        run / "manifest.json",
        {
            "schema_version": "qa-v0",
            "run_id": "run-1",
            "status": "scored",
            "attempts": 1,
            "phases": {
                "prepare": {"status": "completed"},
                "run": {"status": "completed"},
                "score": {"status": "completed"},
            },
            "artifacts": artifact_hashes(run, {"summary.json", "report.md"}),
        },
    )
    (run / "summary.json").write_text('{"overall_attempt_pass_rate": 0.0}\n')
    history = EvaluationHistory(tmp_path)

    row = history.list_runs()[0]

    assert row["valid"] is False
    assert "hash mismatch" in row["error"]


def test_history_rejects_missing_declared_artifact(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    write_json(run / "summary.json", {"overall_attempt_pass_rate": 1.0})
    (run / "report.md").write_text("# Report\n")
    write_json(
        run / "manifest.json",
        {
            "schema_version": "qa-v0",
            "run_id": "run-1",
            "status": "scored",
            "attempts": 1,
            "phases": {
                "prepare": {"status": "completed"},
                "run": {"status": "completed"},
                "score": {"status": "completed"},
            },
            "artifacts": artifact_hashes(run, {"summary.json", "report.md"}),
        },
    )
    (run / "summary.json").unlink()

    row = EvaluationHistory(tmp_path).list_runs()[0]

    assert row["valid"] is False
    assert "required workspace artifact is missing" in row["error"]


def test_history_does_not_follow_run_directory_symlinks(tmp_path):
    external = tmp_path.parent / "external-evaluation-run"
    external.mkdir()
    (tmp_path / "linked").symlink_to(external, target_is_directory=True)

    assert EvaluationHistory(tmp_path).list_runs() == []
