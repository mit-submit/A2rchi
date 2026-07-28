import json
import threading
import uuid

import pytest

from src.evaluation.qa.artifacts import artifact_hashes, read_json, write_json
from src.evaluation.qa.console import EvaluationConsoleService
from src.evaluation.qa.history import EvaluationHistory
from src.evaluation.qa.jobs import EvaluationJobManager, JobConflictError


class _RetryWorkflow:
    def retry_plan(self, _parent_path):
        return {"retry_attempt_ids": ["item-1::attempt-1"]}

    def retry(self, _parent_path, output_dir):
        write_json(output_dir / "summary.json", {"overall_attempt_pass_rate": 1.0})
        (output_dir / "report.md").write_text("# Retry report\n")
        return {
            "schema_version": "qa-v0",
            "run_id": "retry-run",
            "status": "scored",
            "artifacts": artifact_hashes(
                output_dir, {"summary.json", "report.md"}
            ),
        }


class _NoRetryWorkflow:
    def retry_plan(self, _parent_path):
        raise ValueError("evaluation run has no failed attempts to retry")


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
        valid / "console_metadata.json",
        {
            "name": "Retry run",
            "retry_of_history_id": "a" * 24,
            "retry_number": 2,
        },
    )
    write_json(
        valid / "manifest.json",
        {
            "schema_version": "qa-v0",
            "run_id": "run-1",
            "status": "scored",
            "attempts": 2,
            "artifacts": artifact_hashes(
                valid,
                {"summary.json", "report.md", "console_metadata.json"},
            ),
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
    assert valid_row["retry_of_history_id"] == "a" * 24
    assert valid_row["retry_number"] == 2
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


def test_console_retry_keeps_root_name_across_retry_generations(tmp_path):
    service = EvaluationConsoleService(
        tmp_path,
        agent_config_path=tmp_path / "config.yaml",
        agents_dir=tmp_path,
        workflow_factory=_RetryWorkflow,
    )
    parent = service.catalog.runs_dir / "parent"
    parent.mkdir()
    write_json(parent / "summary.json", {"overall_attempt_pass_rate": 0.5})
    (parent / "report.md").write_text("# Parent report\n")
    write_json(
        parent / "console_metadata.json",
        {
            "name": "Original run · retry 1",
            "retry_number": 1,
            "retry_root_name": "Original run",
        },
    )
    write_json(
        parent / "manifest.json",
        {
            "schema_version": "qa-v0",
            "run_id": "parent-run",
            "status": "scored",
            "attempts": 1,
            "artifacts": artifact_hashes(
                parent,
                {"summary.json", "report.md", "console_metadata.json"},
            ),
            "phases": {
                "prepare": {"status": "completed"},
                "run": {"status": "completed"},
                "score": {"status": "completed"},
            },
        },
    )
    history_id = service.history.id_for_path(parent)

    job = service.start_evaluation_retry(history_id)
    completed = service.jobs.wait(job["id"], timeout=2)

    assert completed["status"] == "completed"
    successor = service.history.run_path(completed["result"]["history_id"])
    metadata = read_json(successor / "console_metadata.json")
    assert metadata["name"] == "Original run · retry 2"
    assert metadata["retry_root_name"] == "Original run"
    assert metadata["retry_number"] == 2
    service.jobs.close()


def test_console_retry_without_failures_creates_no_job_or_successor(tmp_path):
    service = EvaluationConsoleService(
        tmp_path,
        agent_config_path=tmp_path / "config.yaml",
        agents_dir=tmp_path,
        workflow_factory=_NoRetryWorkflow,
    )
    parent = service.catalog.runs_dir / "parent"
    parent.mkdir()
    write_json(parent / "summary.json", {"overall_attempt_pass_rate": 1.0})
    (parent / "report.md").write_text("# Complete report\n")
    write_json(
        parent / "manifest.json",
        {
            "schema_version": "qa-v0",
            "run_id": "complete-run",
            "status": "scored",
            "attempts": 1,
            "artifacts": artifact_hashes(parent, {"summary.json", "report.md"}),
            "phases": {
                "prepare": {"status": "completed"},
                "run": {"status": "completed"},
                "score": {"status": "completed"},
            },
        },
    )
    history_id = service.history.id_for_path(parent)

    with pytest.raises(ValueError, match="no failed attempts"):
        service.start_evaluation_retry(history_id)

    assert service.jobs.list() == []
    assert list(service.catalog.runs_dir.iterdir()) == [parent]
    service.jobs.close()


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
