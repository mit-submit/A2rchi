import json
from collections import Counter
from types import SimpleNamespace

import pytest

import src.evaluation.qa.workflow as workflow_module
from src.evaluation.qa.artifacts import read_json, read_jsonl
from src.evaluation.qa.workflow import QAWorkflow


class _EvaluatorFactory:
    def __init__(self, not_mentioned_questions=None):
        self.calls = Counter()
        self.not_mentioned_questions = set(not_mentioned_questions or [])

    def __call__(self, profile):
        factory = self

        class Evaluator:
            def extract_gold(self, question, answer):
                factory.calls["gold"] += 1
                return {
                    "atoms": [
                        {"id": "required", "text": answer, "required": True},
                        {
                            "id": "optional",
                            "text": "Optional detail",
                            "required": False,
                        },
                    ]
                }

            def compare(self, question, gold_atoms, answer):
                factory.calls["compare"] += 1
                if answer == "malformed":
                    return {"wrong": []}
                return {
                    "judgments": [
                        {
                            "atom_id": atom.id,
                            "outcome": (
                                "not_mentioned"
                                if question in factory.not_mentioned_questions
                                else (
                                    "entailed"
                                    if atom.required
                                    else "not_mentioned"
                                )
                            ),
                            "rationale": "deterministic fake",
                        }
                        for atom in gold_atoms
                    ]
                }

        return Evaluator()


class _AgentFactory:
    def __init__(self, failures=None, malformed_questions=None, tool_calls=None):
        self.calls = Counter()
        self.failures = failures or set()
        self.malformed_questions = malformed_questions or set()
        self.tool_calls = tool_calls or []

    def __call__(self, config, spec, pipeline_class):
        factory = self

        class Agent:
            def __init__(self):
                self.tool_calls = []

            def run(self, question):
                self.tool_calls = [dict(call) for call in factory.tool_calls]
                factory.calls[question] += 1
                ordinal = factory.calls[question]
                if (question, ordinal) in factory.failures:
                    raise RuntimeError("agent failed")
                return (
                    "malformed" if question in factory.malformed_questions else "answer"
                )

        return Agent()


@pytest.fixture
def agent_inputs(monkeypatch):
    config = {
        "services": {
            "chat_app": {
                "agent_class": "FakeAgent",
                "default_provider": "fake",
                "default_model": "fake-model",
            }
        }
    }
    spec = SimpleNamespace(tools=["fake"])
    monkeypatch.setattr(
        workflow_module,
        "load_agent_inputs",
        lambda config_path, spec_path: (
            config,
            spec,
            "---\nname: Fake\ntools: [fake]\n---\nPrompt\n",
            object,
        ),
    )
    return config


def _dataset(path):
    path.write_text(
        json.dumps(
            [
                {
                    "id": "inferred",
                    "question": "inferred question",
                    "answer": "expected",
                    "time_sensitive": False,
                },
                {
                    "id": "supplied",
                    "question": "supplied question",
                    "answer": "supplied expected",
                    "time_sensitive": False,
                    "expected_atoms": [
                        {
                            "id": "required",
                            "text": "supplied expected",
                            "required": True,
                        },
                        {
                            "id": "optional",
                            "text": "Optional detail",
                            "required": False,
                        },
                    ],
                },
                {
                    "id": "live",
                    "question": "live question",
                    "answer": "current",
                    "time_sensitive": True,
                },
            ]
        )
    )


def _semantic_artifacts(run_dir):
    artifacts = {
        name: read_jsonl(run_dir / name)
        for name in (
            "prepared_items.jsonl",
            "preparation_results.jsonl",
            "answers.jsonl",
            "evaluation_results.jsonl",
        )
    }
    artifacts["answers.jsonl"] = [
        {key: value for key, value in row.items() if key != "duration_ms"}
        for row in artifacts["answers.jsonl"]
    ]
    return artifacts | {"summary.json": read_json(run_dir / "summary.json")}


def test_composite_and_staged_workflows_are_equivalent_at_four_attempts(
    agent_inputs, tmp_path
):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    staged = tmp_path / "staged"
    composite = tmp_path / "composite"

    staged_evaluator = _EvaluatorFactory()
    staged_agent = _AgentFactory()
    staged_workflow = QAWorkflow(staged_evaluator, staged_agent)
    staged_workflow.prepare(dataset, staged)
    staged_workflow.run(staged, tmp_path / "agent.yaml", tmp_path / "agent.md", 4)
    staged_workflow.score(staged)

    composite_evaluator = _EvaluatorFactory()
    composite_agent = _AgentFactory()
    QAWorkflow(composite_evaluator, composite_agent).composite(
        dataset,
        tmp_path / "agent.yaml",
        tmp_path / "agent.md",
        composite,
        attempts=4,
    )

    assert _semantic_artifacts(staged) == _semantic_artifacts(composite)
    assert staged_evaluator.calls == Counter({"compare": 8, "gold": 1})
    assert staged_agent.calls == Counter(
        {"inferred question": 4, "supplied question": 4}
    )
    summary = read_json(staged / "summary.json")
    assert summary["item_lifecycle_counts"] == {
        "prepared": 2,
        "preparation_failed": 0,
        "skipped_time_sensitive": 1,
    }
    assert summary["attempt_lifecycle_counts"] == {
        "execution_failed": 0,
        "evaluation_failed": 0,
        "scored": 8,
    }
    assert summary["overall_attempt_pass_rate"] == 1.0
    assert all(
        isinstance(row["duration_ms"], int) and row["duration_ms"] >= 0
        for row in read_jsonl(staged / "answers.jsonl")
    )
    assert set(summary["provenance"]) == {
        "agent_config_sha256",
        "agent_spec_sha256",
        "evaluator_profile_sha256",
    }
    manifest = read_json(staged / "manifest.json")
    assert manifest["versions"] == {
        "scoring": "1",
        "prompts": {
            "gold": "qa-gold-atoms-v1",
            "comparator": "qa-answer-comparator-v1",
        },
    }
    assert "code_revision" not in manifest
    assert "sha256" not in manifest["input"]
    assert "sha256" not in manifest["evaluator_profile"]
    assert set(manifest["agent"]) == {
        "agent_class",
        "provider",
        "model",
        "config_artifact",
        "spec_artifact",
    }
    report = (staged / "report.md").read_text()
    assert "- Agent class: `FakeAgent`" in report
    assert "- Agent:" not in report
    assert "fake-model" not in report


def test_failure_accounting_preserves_slots_and_denominators(agent_inputs, tmp_path):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "id": "item",
                    "question": "question",
                    "answer": "expected",
                    "time_sensitive": False,
                    "expected_atoms": [
                        {"id": "required", "text": "expected", "required": True}
                    ],
                },
                {
                    "id": "bad-eval",
                    "question": "bad eval",
                    "answer": "expected",
                    "time_sensitive": False,
                    "expected_atoms": [
                        {"id": "required", "text": "expected", "required": True}
                    ],
                },
            ]
        )
    )
    evaluator = _EvaluatorFactory()
    agent = _AgentFactory(failures={("question", 2)}, malformed_questions={"bad eval"})
    run_dir = tmp_path / "run"

    QAWorkflow(evaluator, agent).composite(
        dataset, tmp_path / "agent.yaml", tmp_path / "agent.md", run_dir, attempts=2
    )

    results = read_jsonl(run_dir / "evaluation_results.jsonl")
    assert Counter(row["status"] for row in results) == Counter(
        {"scored": 1, "execution_failed": 1, "evaluation_failed": 2}
    )
    summary = read_json(run_dir / "summary.json")
    assert summary["quality_accounted_attempts"] == 2
    assert summary["overall_attempt_pass_rate"] == 0.5
    assert summary["item_macro_exclusion_count"] == 1


def test_run_persists_agent_duration_for_success_and_failure(
    agent_inputs, monkeypatch, tmp_path
):
    dataset = tmp_path / "latency-dataset.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "id": "success",
                    "question": "successful question",
                    "answer": "expected",
                    "time_sensitive": False,
                    "expected_atoms": [
                        {"id": "required", "text": "expected", "required": True}
                    ],
                },
                {
                    "id": "failure",
                    "question": "failing question",
                    "answer": "expected",
                    "time_sensitive": False,
                    "expected_atoms": [
                        {"id": "required", "text": "expected", "required": True}
                    ],
                },
            ]
        )
    )
    ticks = iter((10.0, 10.125, 20.0, 20.5))
    monkeypatch.setattr(
        workflow_module, "perf_counter", lambda: next(ticks), raising=False
    )
    run_dir = tmp_path / "run"
    workflow = QAWorkflow(
        _EvaluatorFactory(),
        _AgentFactory(
            failures={("failing question", 1)},
            tool_calls=[
                {
                    "ordinal": 1,
                    "name": "search",
                    "status": "success",
                    "duration_ms": 75,
                }
            ],
        ),
    )
    workflow.prepare(dataset, run_dir)

    workflow.run(run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md")

    answers = {
        row["item_id"]: row for row in read_jsonl(run_dir / "answers.jsonl")
    }
    assert answers["success"]["status"] == "answer_ready"
    assert answers["success"]["duration_ms"] == 125
    assert answers["success"]["tool_calls"] == [
        {
            "ordinal": 1,
            "name": "search",
            "status": "success",
            "duration_ms": 75,
        }
    ]
    assert answers["failure"]["status"] == "execution_failed"
    assert answers["failure"]["duration_ms"] == 500
    assert answers["failure"]["tool_calls"] == answers["success"]["tool_calls"]


def test_run_stops_failure_timer_before_formatting_the_exception(
    agent_inputs, monkeypatch, tmp_path
):
    dataset = tmp_path / "latency-dataset.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "id": "failure",
                    "question": "failing question",
                    "answer": "expected",
                    "time_sensitive": False,
                    "expected_atoms": [
                        {"id": "required", "text": "expected", "required": True}
                    ],
                }
            ]
        )
    )
    clock = {"now": 20.0}
    monkeypatch.setattr(
        workflow_module, "perf_counter", lambda: clock["now"], raising=False
    )

    class SlowStringError(RuntimeError):
        def __str__(self):
            clock["now"] = 21.5
            return "agent failed"

    class FailingAgentFactory:
        def __call__(self, config, spec, pipeline_class):
            class Agent:
                def __init__(self):
                    self.tool_calls = []

                def run(self, question):
                    clock["now"] = 20.5
                    raise SlowStringError()

            return Agent()

    run_dir = tmp_path / "run"
    workflow = QAWorkflow(_EvaluatorFactory(), FailingAgentFactory())
    workflow.prepare(dataset, run_dir)

    workflow.run(run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md")

    [answer] = read_jsonl(run_dir / "answers.jsonl")
    assert answer["duration_ms"] == 500
    assert answer["error"] == {
        "type": "SlowStringError",
        "message": "agent failed",
    }


def test_score_does_not_initialize_evaluator_when_all_executions_failed(
    agent_inputs, tmp_path
):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "id": "item",
                    "question": "question",
                    "answer": "expected",
                    "time_sensitive": False,
                    "expected_atoms": [
                        {"id": "required", "text": "expected", "required": True}
                    ],
                }
            ]
        )
    )
    run_dir = tmp_path / "run"
    setup_workflow = QAWorkflow(
        _EvaluatorFactory(), _AgentFactory(failures={("question", 1)})
    )
    setup_workflow.prepare(dataset, run_dir)
    setup_workflow.run(
        run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md", attempts=1
    )

    def unexpected_evaluator(profile):
        raise AssertionError("evaluator must not be initialized")

    QAWorkflow(unexpected_evaluator, _AgentFactory()).score(run_dir)

    assert read_jsonl(run_dir / "evaluation_results.jsonl")[0]["status"] == (
        "execution_failed"
    )


def test_score_model_initialization_failure_aborts_phase(agent_inputs, tmp_path):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    run_dir = tmp_path / "run"
    setup_workflow = QAWorkflow(_EvaluatorFactory(), _AgentFactory())
    setup_workflow.prepare(dataset, run_dir)
    setup_workflow.run(
        run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md", attempts=1
    )

    def failing_evaluator(profile):
        raise TypeError("temperature is not accepted")

    with pytest.raises(TypeError, match="temperature is not accepted"):
        QAWorkflow(failing_evaluator, _AgentFactory()).score(run_dir)

    for name in (
        "evaluation_results.jsonl",
        "summary.json",
        "report.md",
    ):
        assert not (run_dir / name).exists()


def test_run_runtime_construction_failure_aborts_without_attempt_artifacts(
    agent_inputs, tmp_path
):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    run_dir = tmp_path / "run"
    workflow = QAWorkflow(
        _EvaluatorFactory(),
        lambda config, spec, pipeline_class: (_ for _ in ()).throw(
            RuntimeError("agent runtime failed to initialize")
        ),
    )
    workflow.prepare(dataset, run_dir)

    with pytest.raises(RuntimeError, match="runtime failed to initialize"):
        workflow.run(run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md")

    assert not (run_dir / "answers.jsonl").exists()


def test_hash_tamper_fails_before_agent_call(agent_inputs, tmp_path):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    run_dir = tmp_path / "run"
    agent = _AgentFactory()
    workflow = QAWorkflow(_EvaluatorFactory(), agent)
    workflow.prepare(dataset, run_dir)
    with (run_dir / "prepared_items.jsonl").open("a") as handle:
        handle.write("{}\n")

    with pytest.raises(ValueError, match="hash mismatch"):
        workflow.run(run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md")

    assert agent.calls == Counter()


def test_prepare_validates_all_rows_before_evaluator_calls(tmp_path):
    dataset = tmp_path / "invalid.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "question": "valid",
                    "answer": "valid",
                    "time_sensitive": False,
                },
                {
                    "question": "invalid",
                    "answer": "invalid",
                    "time_sensitive": False,
                    "unexpected": "not allowed",
                },
            ]
        )
    )
    evaluator = _EvaluatorFactory()

    with pytest.raises(ValueError, match="unknown field.*unexpected"):
        QAWorkflow(evaluator, _AgentFactory()).prepare(dataset, tmp_path / "run")

    assert evaluator.calls == Counter()
    assert not (tmp_path / "run").exists()


def test_prepare_overwrite_preserves_unknown_files_and_removes_downstream(
    agent_inputs, tmp_path
):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    run_dir = tmp_path / "run"
    workflow = QAWorkflow(_EvaluatorFactory(), _AgentFactory())
    workflow.composite(dataset, tmp_path / "agent.yaml", tmp_path / "agent.md", run_dir)
    unknown = run_dir / "operator-notes.txt"
    unknown.write_text("keep me")

    workflow.prepare(dataset, run_dir, overwrite=True)

    assert unknown.read_text() == "keep me"
    assert not (run_dir / "answers.jsonl").exists()
    assert not (run_dir / "summary.json").exists()


def test_prepare_rejects_any_existing_owned_artifact_without_overwrite(tmp_path):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "report.md").write_text("stale score output")

    with pytest.raises(ValueError, match="report.md.*--overwrite"):
        QAWorkflow(_EvaluatorFactory(), _AgentFactory()).prepare(dataset, run_dir)


def test_prepare_does_not_recursively_delete_directory_at_artifact_path(tmp_path):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    run_dir = tmp_path / "run"
    nested = run_dir / "answers.jsonl"
    nested.mkdir(parents=True)
    marker = nested / "operator-data"
    marker.write_text("keep me")

    with pytest.raises(ValueError, match="artifact path.*directories"):
        QAWorkflow(_EvaluatorFactory(), _AgentFactory()).prepare(
            dataset, run_dir, overwrite=True
        )

    assert marker.read_text() == "keep me"


def test_prepare_preflight_failure_preserves_overwritten_workspace(tmp_path):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    report = run_dir / "report.md"
    report.write_text("previous report")

    def failing_evaluator(profile):
        raise RuntimeError("provider configuration failed")

    with pytest.raises(RuntimeError, match="provider configuration failed"):
        QAWorkflow(failing_evaluator, _AgentFactory()).prepare(
            dataset, run_dir, overwrite=True
        )

    assert report.read_text() == "previous report"


def test_run_rejects_downstream_score_artifact_without_overwrite(
    agent_inputs, tmp_path
):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    run_dir = tmp_path / "run"
    workflow = QAWorkflow(_EvaluatorFactory(), _AgentFactory())
    workflow.prepare(dataset, run_dir)
    (run_dir / "report.md").write_text("stale score output")

    with pytest.raises(ValueError, match="report.md.*--overwrite"):
        workflow.run(run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md")


def test_score_rejects_any_existing_score_artifact_without_overwrite(
    agent_inputs, tmp_path
):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    run_dir = tmp_path / "run"
    workflow = QAWorkflow(_EvaluatorFactory(), _AgentFactory())
    workflow.prepare(dataset, run_dir)
    workflow.run(run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md")
    (run_dir / "report.md").write_text("stale score output")

    with pytest.raises(ValueError, match="report.md.*--overwrite"):
        workflow.score(run_dir)


def test_score_preflight_failure_preserves_overwritten_report(agent_inputs, tmp_path):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    run_dir = tmp_path / "run"
    workflow = QAWorkflow(_EvaluatorFactory(), _AgentFactory())
    workflow.prepare(dataset, run_dir)
    workflow.run(run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md")
    answers_path = run_dir / "answers.jsonl"
    answers = read_jsonl(answers_path)
    answers[0]["status"] = "invalid"
    answers_path.write_text(
        "".join(json.dumps(row) + "\n" for row in answers), encoding="utf-8"
    )
    manifest = read_json(run_dir / "manifest.json")
    manifest["artifacts"]["answers.jsonl"] = workflow_module.sha256_file(answers_path)
    workflow_module.write_json(run_dir / "manifest.json", manifest)
    report = run_dir / "report.md"
    report.write_text("previous report")

    with pytest.raises(ValueError, match="non-terminal or unsupported"):
        workflow.score(run_dir, overwrite=True)

    assert report.read_text() == "previous report"


def test_run_persists_admin_visible_answer_without_redaction(agent_inputs, tmp_path):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    run_dir = tmp_path / "run"

    class SecretAgentFactory:
        def __call__(self, config, spec, pipeline_class):
            class Agent:
                tool_calls = []

                def run(self, question):
                    return "answer configured-secret-value"

            return Agent()

    workflow = QAWorkflow(_EvaluatorFactory(), SecretAgentFactory())
    workflow.prepare(dataset, run_dir)
    workflow.run(run_dir, tmp_path / "agent.yaml", tmp_path / "agent.md")

    answers = read_jsonl(run_dir / "answers.jsonl")
    assert {row["answer"] for row in answers} == {"answer configured-secret-value"}


def test_retry_creates_complete_successor_and_invokes_only_failed_phases(
    agent_inputs, monkeypatch, tmp_path
):
    dataset = tmp_path / "retry-dataset.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "id": item_id,
                    "question": question,
                    "answer": "expected",
                    "time_sensitive": False,
                    "expected_atoms": [
                        {"id": "required", "text": "expected", "required": True}
                    ],
                }
                for item_id, question in (
                    ("scored", "scored question"),
                    ("execution", "execution question"),
                    ("evaluation", "evaluation question"),
                )
            ]
        )
    )
    parent = tmp_path / "parent"
    parent_agent = _AgentFactory(
        failures={("execution question", 1)},
        malformed_questions={"evaluation question"},
        tool_calls=[
            {
                "ordinal": 1,
                "name": "parent-tool",
                "status": "success",
                "duration_ms": 25,
            }
        ],
    )
    QAWorkflow(
        _EvaluatorFactory(not_mentioned_questions={"scored question"}),
        parent_agent,
    ).composite(
        dataset,
        tmp_path / "agent.yaml",
        tmp_path / "agent.md",
        parent,
    )
    parent_bytes = {
        path.name: path.read_bytes() for path in parent.iterdir() if path.is_file()
    }
    parent_answers = {
        row["attempt_id"]: row for row in read_jsonl(parent / "answers.jsonl")
    }
    parent_results = {
        row["attempt_id"]: row
        for row in read_jsonl(parent / "evaluation_results.jsonl")
    }
    retry_agent = _AgentFactory(
        tool_calls=[
            {
                "ordinal": 1,
                "name": "retry-tool",
                "status": "success",
                "duration_ms": 40,
            }
        ]
    )

    class RecoveringEvaluatorFactory:
        def __init__(self):
            self.calls = Counter()

        def __call__(self, profile):
            factory = self

            class Evaluator:
                def compare(self, question, gold_atoms, answer):
                    factory.calls[question] += 1
                    return {
                        "judgments": [
                            {
                                "atom_id": atom.id,
                                "outcome": "entailed",
                                "rationale": "retry succeeded",
                            }
                            for atom in gold_atoms
                        ]
                    }

            return Evaluator()

    retry_evaluator = RecoveringEvaluatorFactory()
    successor = tmp_path / "successor"
    retry_workflow = QAWorkflow(retry_evaluator, retry_agent)
    ticks = iter((30.0, 30.4))
    monkeypatch.setattr(
        workflow_module, "perf_counter", lambda: next(ticks), raising=False
    )

    manifest = retry_workflow.retry(parent, successor)

    successor_answers = {
        row["attempt_id"]: row for row in read_jsonl(successor / "answers.jsonl")
    }
    successor_results = {
        row["attempt_id"]: row
        for row in read_jsonl(successor / "evaluation_results.jsonl")
    }
    assert retry_agent.calls == Counter({"execution question": 1})
    assert retry_evaluator.calls == Counter(
        {"execution question": 1, "evaluation question": 1}
    )
    assert parent_results["scored-attempt-1"]["passed"] is False
    assert successor_answers["scored-attempt-1"] == parent_answers[
        "scored-attempt-1"
    ]
    assert successor_results["scored-attempt-1"] == parent_results[
        "scored-attempt-1"
    ]
    assert successor_answers["evaluation-attempt-1"] == parent_answers[
        "evaluation-attempt-1"
    ]
    assert successor_answers["execution-attempt-1"]["duration_ms"] == 400
    assert parent_answers["execution-attempt-1"]["tool_calls"][0]["name"] == (
        "parent-tool"
    )
    assert successor_answers["execution-attempt-1"]["tool_calls"] == [
        {
            "ordinal": 1,
            "name": "retry-tool",
            "status": "success",
            "duration_ms": 40,
        }
    ]
    assert {row["status"] for row in successor_results.values()} == {"scored"}
    assert manifest["retry"] == {
        "parent_run_id": read_json(parent / "manifest.json")["run_id"],
        "retry_attempt_ids": [
            "execution-attempt-1",
            "evaluation-attempt-1",
        ],
        "execution_attempt_ids": ["execution-attempt-1"],
        "evaluation_attempt_ids": ["evaluation-attempt-1"],
        "carried_forward_attempt_ids": ["scored-attempt-1"],
    }
    assert read_json(successor / "summary.json")["attempt_lifecycle_counts"] == {
        "execution_failed": 0,
        "evaluation_failed": 0,
        "scored": 3,
    }
    for artifact in (
        "input.snapshot.json",
        "prepared_items.jsonl",
        "preparation_results.jsonl",
        "evaluator_profile.resolved.yaml",
        "agent_config.resolved.yaml",
        "agent_spec.resolved.md",
    ):
        assert (successor / artifact).read_bytes() == (parent / artifact).read_bytes()
    assert {
        path.name: path.read_bytes() for path in parent.iterdir() if path.is_file()
    } == parent_bytes
    with pytest.raises(ValueError, match="no failed attempts"):
        retry_workflow.retry_plan(successor)


def test_retry_rejects_tampered_parent_before_provider_or_output(
    agent_inputs, tmp_path
):
    dataset = tmp_path / "dataset.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "id": "item",
                    "question": "question",
                    "answer": "expected",
                    "time_sensitive": False,
                    "expected_atoms": [
                        {"id": "required", "text": "expected", "required": True}
                    ],
                }
            ]
        )
    )
    parent = tmp_path / "parent"
    QAWorkflow(
        _EvaluatorFactory(),
        _AgentFactory(failures={("question", 1)}),
    ).composite(
        dataset,
        tmp_path / "agent.yaml",
        tmp_path / "agent.md",
        parent,
    )
    with (parent / "evaluation_results.jsonl").open("a") as handle:
        handle.write("{}\n")
    evaluator = _EvaluatorFactory()
    agent = _AgentFactory()
    successor = tmp_path / "successor"

    with pytest.raises(ValueError, match="hash mismatch"):
        QAWorkflow(evaluator, agent).retry(parent, successor)

    assert evaluator.calls == Counter()
    assert agent.calls == Counter()
    assert not successor.exists()


def test_composite_validates_selected_agent_inputs_before_gold_provider_call(
    monkeypatch, tmp_path
):
    dataset = tmp_path / "dataset.json"
    _dataset(dataset)
    evaluator = _EvaluatorFactory()
    monkeypatch.setattr(
        workflow_module,
        "load_agent_inputs",
        lambda config_path, spec_path: (_ for _ in ()).throw(
            ValueError("invalid agent")
        ),
    )

    with pytest.raises(ValueError, match="invalid agent"):
        QAWorkflow(evaluator, _AgentFactory()).composite(
            dataset,
            tmp_path / "agent.yaml",
            tmp_path / "agent.md",
            tmp_path / "run",
        )

    assert evaluator.calls == Counter()
    assert not (tmp_path / "run").exists()
