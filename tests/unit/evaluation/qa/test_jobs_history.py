import json
import threading
import uuid

import pytest

import src.evaluation.qa.console as console_module
import src.evaluation.qa.history as history_module
from src.evaluation.qa.artifacts import (artifact_hashes, read_json,
                                         write_json, write_jsonl)
from src.evaluation.qa.console import EvaluationConsoleService
from src.evaluation.qa.history import EvaluationHistory
from src.evaluation.qa.jobs import EvaluationJobManager, JobConflictError


class _RetryWorkflow:
    def retry_plan(self, _parent_path):
        return {"retry_attempt_ids": ["item-1::attempt-1"]}

    def retry(self, parent_path, output_dir):
        for name in ("input.snapshot.json", "preparation.jsonl"):
            (output_dir / name).write_bytes((parent_path / name).read_bytes())
        write_json(output_dir / "summary.json", {"overall_attempt_pass_rate": 1.0})
        (output_dir / "report.md").write_text("# Retry report\n")
        return {
            "schema_version": "qa-v1",
            "run_id": "retry-run",
            "status": "scored",
            "input": {"snapshot": "input.snapshot.json"},
            "attempts": 1,
            "phases": {
                "prepare": {"status": "completed", "input_items": 1},
                "run": {"status": "completed"},
                "score": {"status": "completed"},
            },
            "artifacts": artifact_hashes(
                output_dir,
                {
                    "input.snapshot.json",
                    "preparation.jsonl",
                    "summary.json",
                    "report.md",
                },
            ),
        }


class _NoRetryWorkflow:
    def retry_plan(self, _parent_path):
        raise ValueError("evaluation run has no failed attempts to retry")


def _write_preparation_artifacts(run_dir):
    write_json(
        run_dir / "input.snapshot.json",
        [
            {
                "id": "item",
                "question": "Question",
                "answer": "Answer",
                "time_sensitive": True,
            }
        ],
    )
    write_jsonl(
        run_dir / "preparation.jsonl",
        [
            {
                "item_id": "item",
                "status": "skipped_time_sensitive",
                "category": None,
                "answer_mode": None,
                "answer_source": None,
            }
        ],
    )
    return {"input.snapshot.json", "preparation.jsonl"}


def _write_legacy_prepared_workspace(run_dir):
    write_json(
        run_dir / "input.snapshot.json",
        [
            {
                "id": "item",
                "question": "Question",
                "answer": "Answer",
                "time_sensitive": False,
            }
        ],
    )
    prepared = {
        "item_id": "item",
        "question": "Question",
        "answer": "Answer",
        "time_sensitive": False,
        "category": None,
        "answer_mode": None,
        "answer_source": None,
        "atom_source": "supplied",
        "gold_atoms": [{"id": "A1", "text": "Answer", "required": True}],
    }
    write_jsonl(run_dir / "prepared_items.jsonl", [prepared])
    write_jsonl(
        run_dir / "preparation_results.jsonl",
        [
            {
                "item_id": "item",
                "status": "prepared",
                "category": None,
                "answer_mode": None,
                "answer_source": None,
            }
        ],
    )
    artifacts = {
        "input.snapshot.json",
        "prepared_items.jsonl",
        "preparation_results.jsonl",
    }
    write_json(
        run_dir / "manifest.json",
        {
            "schema_version": "qa-v0",
            "run_id": "legacy-run",
            "status": "prepared",
            "input": {"snapshot": "input.snapshot.json"},
            "artifacts": artifact_hashes(run_dir, artifacts),
            "phases": {
                "prepare": {"status": "completed", "input_items": 1},
            },
        },
    )
    return prepared


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


def test_console_atom_generation_constructs_evaluator_directly(monkeypatch, tmp_path):
    profiles = []

    class Evaluator:
        def extract_gold(self, question, answer):
            return {
                "atoms": [
                    {"id": "A1", "text": answer, "required": True},
                ]
            }

    def evaluator_runtime(profile):
        profiles.append(profile)
        return Evaluator()

    monkeypatch.setattr(console_module, "LangChainEvaluatorRuntime", evaluator_runtime)
    service = EvaluationConsoleService(
        tmp_path,
        agent_config_path=tmp_path / "config.yaml",
        agents_dir=tmp_path,
    )
    dataset, _created = service.catalog.import_dataset(
        "Dataset",
        "dataset.json",
        json.dumps(
            [
                {
                    "id": "item",
                    "question": "Question",
                    "answer": "Answer",
                    "time_sensitive": False,
                }
            ]
        ).encode(),
    )

    job = service.start_atom_generation(dataset["id"], "builtin")
    completed = service.jobs.wait(job["id"], timeout=2)
    draft = service.catalog.get_atom_draft(completed["result"]["draft_id"])

    assert completed["status"] == "completed"
    assert len(profiles) == 1
    assert draft["items"][0]["atoms"] == [
        {"id": "A1", "text": "Answer", "required": True},
    ]
    service.jobs.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_workers", 0),
        ("run_workers", 17),
        ("run_workers", True),
        ("score_workers", 0),
        ("score_workers", 17),
        ("score_workers", "2"),
    ],
)
def test_console_rejects_invalid_phase_workers_before_catalog_or_job(
    field, value, tmp_path
):
    service = EvaluationConsoleService(
        tmp_path,
        agent_config_path=tmp_path / "config.yaml",
        agents_dir=tmp_path,
    )
    values = {"run_workers": 1, "score_workers": 1, field: value}

    with pytest.raises(ValueError, match=f"{field} must be an integer from 1 to 16"):
        service.start_evaluation(
            name="Run",
            dataset_id="missing",
            profile_id="builtin",
            agent_spec="agent.md",
            attempts=1,
            **values,
        )

    assert service.jobs.list() == []
    service.jobs.close()


def test_console_persists_and_passes_phase_workers(tmp_path):
    calls = []

    class Workflow:
        def composite(self, **kwargs):
            calls.append(kwargs)
            return {
                "run_id": "run-1",
                "status": "scored",
                "artifacts": {},
            }

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "agent.md").write_text(
        "---\nname: Agent\ntools: [search]\n---\nPrompt\n"
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text("services: {}\n")
    service = EvaluationConsoleService(
        tmp_path / "console",
        agent_config_path=config_path,
        agents_dir=agents_dir,
        workflow_factory=Workflow,
    )
    dataset, _created = service.catalog.import_dataset(
        "Dataset",
        "dataset.json",
        b'[{"id":"item","question":"Q","answer":"A","time_sensitive":false}]',
    )

    job = service.start_evaluation(
        name="Parallel run",
        dataset_id=dataset["id"],
        profile_id="builtin",
        agent_spec="agent.md",
        attempts=2,
        run_workers=4,
        score_workers=3,
    )
    completed = service.jobs.wait(job["id"], timeout=2)

    assert completed["status"] == "completed"
    assert job["context"]["run_workers"] == 4
    assert job["context"]["score_workers"] == 3
    assert calls[0]["run_workers"] == 4
    assert calls[0]["score_workers"] == 3
    metadata = read_json(
        service.catalog.runs_dir
        / job["context"]["workspace_id"]
        / "console_metadata.json"
    )
    assert metadata["run_workers"] == 4
    assert metadata["score_workers"] == 3
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
    preparation_artifacts = _write_preparation_artifacts(valid)
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
            "schema_version": "qa-v1",
            "run_id": "run-1",
            "status": "scored",
            "attempts": 2,
            "input": {"snapshot": "input.snapshot.json"},
            "artifacts": artifact_hashes(
                valid,
                preparation_artifacts
                | {"summary.json", "report.md", "console_metadata.json"},
            ),
            "phases": {
                "prepare": {"status": "completed", "input_items": 1},
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


def test_history_reads_legacy_v0_runs_without_rewriting_artifacts(tmp_path):
    run = tmp_path / "legacy"
    run.mkdir()
    write_json(
        run / "input.snapshot.json",
        [
            {
                "id": "prepared",
                "question": "Question",
                "answer": "Answer",
                "time_sensitive": False,
            },
            {
                "id": "skipped",
                "question": "Current question",
                "answer": "Current answer",
                "time_sensitive": True,
            },
            {
                "id": "failed",
                "question": "Failed question",
                "answer": "Failed answer",
                "time_sensitive": False,
            },
        ],
    )
    write_jsonl(
        run / "prepared_items.jsonl",
        [
            {
                "item_id": "prepared",
                "question": "Question",
                "answer": "Answer",
                "time_sensitive": False,
                "category": "general",
                "answer_mode": None,
                "answer_source": None,
                "atom_source": "supplied",
                "gold_atoms": [
                    {"id": "A1", "text": "Answer", "required": True}
                ],
            }
        ],
    )
    write_jsonl(
        run / "preparation_results.jsonl",
        [
            {
                "item_id": "prepared",
                "status": "prepared",
                "category": "general",
                "answer_mode": None,
                "answer_source": None,
            },
            {
                "item_id": "skipped",
                "status": "skipped_time_sensitive",
                "category": "live",
                "answer_mode": None,
                "answer_source": None,
            },
            {
                "item_id": "failed",
                "status": "preparation_failed",
                "category": None,
                "answer_mode": None,
                "answer_source": None,
                "error": "provider unavailable",
            },
        ],
    )
    write_json(run / "summary.json", {"overall_attempt_pass_rate": 1.0})
    (run / "report.md").write_text("# Legacy report\n")
    artifacts = {
        "input.snapshot.json",
        "prepared_items.jsonl",
        "preparation_results.jsonl",
        "summary.json",
        "report.md",
    }
    write_json(
        run / "manifest.json",
        {
            "schema_version": "qa-v0",
            "run_id": "legacy-run",
            "status": "scored",
            "attempts": 1,
            "input": {"snapshot": "input.snapshot.json"},
            "artifacts": artifact_hashes(run, artifacts),
            "phases": {
                "prepare": {"status": "completed", "input_items": 3},
                "run": {"status": "completed"},
                "score": {"status": "completed"},
            },
        },
    )
    original_manifest = (run / "manifest.json").read_bytes()
    history = EvaluationHistory(tmp_path)

    rows = history.list_runs()
    payload = history.get_run(history.id_for_path(run))

    assert rows == [
        {
            "id": history.id_for_path(run),
            "run_id": "legacy-run",
            "name": "legacy-run",
            "status": "scored",
            "created_at": "",
            "dataset_id": None,
            "dataset_name": None,
            "profile_id": None,
            "profile_name": None,
            "agent_spec": None,
            "attempts": 1,
            "retry_of_history_id": None,
            "retry_number": None,
            "overall_attempt_pass_rate": 1.0,
            "schema_version": "qa-v0",
            "capabilities": {"retry_failed": False},
            "valid": True,
        }
    ]
    assert payload["preparation"] == [
        {
            "item_id": "prepared",
            "status": "prepared",
            "category": "general",
            "answer_mode": None,
            "answer_source": None,
            "question": "Question",
            "answer": "Answer",
            "time_sensitive": False,
            "atom_source": "supplied",
            "gold_atoms": [{"id": "A1", "text": "Answer", "required": True}],
        },
        {
            "item_id": "skipped",
            "status": "skipped_time_sensitive",
            "category": "live",
            "answer_mode": None,
            "answer_source": None,
        },
        {
            "item_id": "failed",
            "status": "preparation_failed",
            "category": None,
            "answer_mode": None,
            "answer_source": None,
            "error": "provider unavailable",
        },
    ]
    assert payload["prepared_items"] == [payload["preparation"][0]]
    assert payload["capabilities"] == {"retry_failed": False}
    assert history.get_report(history.id_for_path(run)) == "# Legacy report\n"
    assert (run / "manifest.json").read_bytes() == original_manifest
    assert not (run / "preparation.jsonl").exists()


def test_history_rejects_tampered_legacy_v0_preparation(tmp_path):
    run = tmp_path / "legacy"
    run.mkdir()
    prepared = _write_legacy_prepared_workspace(run)
    prepared["answer"] = "Tampered"
    write_jsonl(run / "prepared_items.jsonl", [prepared])
    history = EvaluationHistory(tmp_path)

    with pytest.raises(
        ValueError,
        match="workspace artifact hash mismatch: prepared_items.jsonl",
    ):
        history.get_run(history.id_for_path(run))


def test_console_rejects_legacy_v0_retry_before_workflow_or_job(tmp_path):
    workflow_constructed = False

    def workflow_factory():
        nonlocal workflow_constructed
        workflow_constructed = True
        return _RetryWorkflow()

    service = EvaluationConsoleService(
        tmp_path,
        agent_config_path=tmp_path / "config.yaml",
        agents_dir=tmp_path,
        workflow_factory=workflow_factory,
    )
    run = service.catalog.runs_dir / "legacy"
    run.mkdir()
    _write_legacy_prepared_workspace(run)

    with pytest.raises(ValueError, match="legacy evaluation runs cannot be retried"):
        service.start_evaluation_retry(service.history.id_for_path(run))

    assert workflow_constructed is False
    assert service.jobs.list() == []
    service.jobs.close()


def test_history_derives_prepared_items_from_canonical_preparation(
    monkeypatch, tmp_path
):
    run = tmp_path / "run"
    run.mkdir()
    write_json(
        run / "input.snapshot.json",
        [
            {
                "id": "item",
                "question": "Question",
                "answer": "Answer",
                "time_sensitive": False,
                "expected_atoms": [
                    {"id": "A1", "text": "Answer", "required": True}
                ],
            }
        ],
    )
    preparation = [
        {
            "item_id": "item",
            "status": "prepared",
            "category": None,
            "answer_mode": None,
            "answer_source": None,
            "question": "Question",
            "answer": "Answer",
            "time_sensitive": False,
            "atom_source": "supplied",
            "gold_atoms": [{"id": "A1", "text": "Answer", "required": True}],
        }
    ]
    write_jsonl(run / "preparation.jsonl", preparation)
    write_json(
        run / "manifest.json",
        {
            "schema_version": "qa-v1",
            "run_id": "run-1",
            "status": "prepared",
            "input": {"snapshot": "input.snapshot.json"},
            "artifacts": artifact_hashes(
                run, {"input.snapshot.json", "preparation.jsonl"}
            ),
            "phases": {
                "prepare": {"status": "completed", "input_items": 1}
            },
        },
    )
    history = EvaluationHistory(tmp_path)
    history_id = history.id_for_path(run)
    monkeypatch.setattr(
        history_module,
        "load_dataset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("history must consume preparation.jsonl directly")
        ),
        raising=False,
    )

    payload = history.get_run(history_id)

    assert payload["preparation"] == preparation
    assert payload["prepared_items"] == preparation


def test_history_exposes_complete_tool_call_trace_unchanged(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    write_json(
        run / "input.snapshot.json",
        [
            {
                "id": "item",
                "question": "Question",
                "answer": "Answer",
                "time_sensitive": False,
                "expected_atoms": [{"id": "A1", "text": "Answer", "required": True}],
            }
        ],
    )
    write_jsonl(
        run / "preparation.jsonl",
        [
            {
                "item_id": "item",
                "status": "prepared",
                "category": None,
                "answer_mode": None,
                "answer_source": None,
                "question": "Question",
                "answer": "Answer",
                "time_sensitive": False,
                "atom_source": "supplied",
                "gold_atoms": [{"id": "A1", "text": "Answer", "required": True}],
            }
        ],
    )
    tool_calls = [
        {
            "ordinal": 1,
            "name": "search",
            "status": "success",
            "query": '{"query": "complete"}',
            "response": "complete response",
            "duration_ms": 13,
        },
        {
            "ordinal": 2,
            "name": "unfinished",
            "status": "incomplete",
            "query": "complete unfinished query",
        },
    ]
    write_jsonl(
        run / "answers.jsonl",
        [
            {
                "item_id": "item",
                "attempt_id": "item-attempt-1",
                "ordinal": 1,
                "status": "answer_ready",
                "duration_ms": 25,
                "tool_calls": tool_calls,
                "answer": "Answer",
            }
        ],
    )
    artifacts = {"input.snapshot.json", "preparation.jsonl", "answers.jsonl"}
    write_json(
        run / "manifest.json",
        {
            "schema_version": "qa-v1",
            "run_id": "run-1",
            "status": "run_completed",
            "attempts": 1,
            "input": {"snapshot": "input.snapshot.json"},
            "artifacts": artifact_hashes(run, artifacts),
            "phases": {
                "prepare": {"status": "completed", "input_items": 1},
                "run": {"status": "completed"},
            },
        },
    )
    history = EvaluationHistory(tmp_path)

    payload = history.get_run(history.id_for_path(run))

    assert payload["answers"][0]["tool_calls"] == tool_calls


def test_history_rejects_invalid_tool_trace_at_artifact_boundary(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    artifacts = _write_preparation_artifacts(run)
    write_jsonl(
        run / "answers.jsonl",
        [
            {
                "item_id": "item",
                "attempt_id": "item-attempt-1",
                "ordinal": 1,
                "status": "answer_ready",
                "duration_ms": 25,
                "tool_calls": [
                    {
                        "ordinal": 1,
                        "name": "search",
                        "status": "success",
                        "query": "missing terminal response",
                    }
                ],
                "answer": "Answer",
            }
        ],
    )
    artifacts.add("answers.jsonl")
    write_json(
        run / "manifest.json",
        {
            "schema_version": "qa-v1",
            "run_id": "run-1",
            "status": "run_completed",
            "attempts": 1,
            "input": {"snapshot": "input.snapshot.json"},
            "artifacts": artifact_hashes(run, artifacts),
            "phases": {
                "prepare": {"status": "completed", "input_items": 1},
                "run": {"status": "completed"},
            },
        },
    )
    history = EvaluationHistory(tmp_path)

    with pytest.raises(ValueError, match="successful tool-call records require"):
        history.get_run(history.id_for_path(run))


def test_history_rejects_tampered_declared_artifacts(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    preparation_artifacts = _write_preparation_artifacts(run)
    write_json(run / "summary.json", {"overall_attempt_pass_rate": 1.0})
    (run / "report.md").write_text("# Report\n")
    write_json(
        run / "manifest.json",
        {
            "schema_version": "qa-v1",
            "run_id": "run-1",
            "status": "scored",
            "attempts": 1,
            "input": {"snapshot": "input.snapshot.json"},
            "phases": {
                "prepare": {"status": "completed", "input_items": 1},
                "run": {"status": "completed"},
                "score": {"status": "completed"},
            },
            "artifacts": artifact_hashes(
                run, preparation_artifacts | {"summary.json", "report.md"}
            ),
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
    preparation_artifacts = _write_preparation_artifacts(parent)
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
            "schema_version": "qa-v1",
            "run_id": "parent-run",
            "status": "scored",
            "attempts": 1,
            "input": {"snapshot": "input.snapshot.json"},
            "artifacts": artifact_hashes(
                parent,
                preparation_artifacts
                | {"summary.json", "report.md", "console_metadata.json"},
            ),
            "phases": {
                "prepare": {"status": "completed", "input_items": 1},
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
    preparation_artifacts = _write_preparation_artifacts(parent)
    write_json(parent / "summary.json", {"overall_attempt_pass_rate": 1.0})
    (parent / "report.md").write_text("# Complete report\n")
    write_json(
        parent / "manifest.json",
        {
            "schema_version": "qa-v1",
            "run_id": "complete-run",
            "status": "scored",
            "attempts": 1,
            "input": {"snapshot": "input.snapshot.json"},
            "artifacts": artifact_hashes(
                parent, preparation_artifacts | {"summary.json", "report.md"}
            ),
            "phases": {
                "prepare": {"status": "completed", "input_items": 1},
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
    preparation_artifacts = _write_preparation_artifacts(run)
    write_json(run / "summary.json", {"overall_attempt_pass_rate": 1.0})
    (run / "report.md").write_text("# Report\n")
    write_json(
        run / "manifest.json",
        {
            "schema_version": "qa-v1",
            "run_id": "run-1",
            "status": "scored",
            "attempts": 1,
            "input": {"snapshot": "input.snapshot.json"},
            "phases": {
                "prepare": {"status": "completed", "input_items": 1},
                "run": {"status": "completed"},
                "score": {"status": "completed"},
            },
            "artifacts": artifact_hashes(
                run, preparation_artifacts | {"summary.json", "report.md"}
            ),
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
