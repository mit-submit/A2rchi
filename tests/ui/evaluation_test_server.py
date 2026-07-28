# isort: skip_file
"""Deterministic local runtime for evaluation-console browser tests."""

import os
import time
from collections import Counter
from pathlib import Path

from flask import Flask

from src.evaluation.qa.artifacts import (
    artifact_hashes,
    read_json,
    read_jsonl,
    write_json,
    write_jsonl,
    write_text,
)
from src.evaluation.qa.console import EvaluationConsoleService
from src.interfaces.chat_app.evaluation_routes import register_evaluations


class FakeEvaluator:
    atom_calls = Counter()

    def extract_gold(self, question, answer):
        self.atom_calls[question] += 1
        if question == "Recover this atom?" and self.atom_calls[question] == 1:
            raise RuntimeError("Deterministic first atom attempt failed.")
        if question == "Recover this atom?":
            time.sleep(0.4)
        return {
            "atoms": [
                {
                    "id": "A1",
                    "text": answer.strip().splitlines()[0][:240],
                    "required": True,
                }
            ]
        }


class FakeWorkflow:
    evaluator_factory = staticmethod(lambda profile: FakeEvaluator())

    def composite(
        self,
        dataset,
        agent_config,
        agent_spec,
        output_dir,
        evaluator_profile_path=None,
        attempts=1,
        overwrite=False,
    ):
        from src.evaluation.qa.validation import load_dataset

        items = [item for item in load_dataset(dataset)[1] if not item.time_sensitive]
        run_id = "browser-e2e-run"
        prepared = [
            {
                "item_id": item.id,
                "question": item.question,
                "answer": item.answer,
                "gold_atoms": [atom.to_dict() for atom in item.expected_atoms],
            }
            for item in items
        ]
        answers = []
        results = []
        for item in items:
            for ordinal in range(1, attempts + 1):
                attempt_id = f"{item.id}-attempt-{ordinal}"
                base = {
                    "item_id": item.id,
                    "attempt_id": attempt_id,
                    "ordinal": ordinal,
                }
                if item.id == "retry-execution":
                    answers.append(
                        {
                            **base,
                            "status": "execution_failed",
                            "duration_ms": 450 + ordinal,
                            "error": {
                                "type": "RuntimeError",
                                "message": "Deterministic execution failure.",
                            },
                        }
                    )
                    results.append(
                        {
                            **base,
                            "status": "execution_failed",
                            "error": {
                                "type": "RuntimeError",
                                "message": "Deterministic execution failure.",
                            },
                        }
                    )
                    continue
                answers.append(
                    {
                        **base,
                        "status": "answer_ready",
                        "duration_ms": 450 + ordinal,
                        "answer": item.answer,
                    }
                )
                if item.id == "retry-evaluation":
                    results.append(
                        {
                            **base,
                            "status": "evaluation_failed",
                            "error": "Deterministic comparison failure.",
                        }
                    )
                    continue
                results.append(
                    {
                        **base,
                        "status": "scored",
                        "answer": item.answer,
                        "passed": True,
                        "atom_score": 1.0,
                        "required_atom_recall": 1.0,
                        "judgments": [
                            {
                                "atom_id": atom.id,
                                "outcome": "entailed",
                                "rationale": "Deterministic browser fixture.",
                            }
                            for atom in item.expected_atoms
                        ],
                    }
                )
        write_jsonl(output_dir / "prepared_items.jsonl", prepared)
        write_jsonl(
            output_dir / "preparation_results.jsonl",
            [{"item_id": item.id, "status": "prepared"} for item in items],
        )
        write_jsonl(output_dir / "answers.jsonl", answers)
        write_jsonl(output_dir / "evaluation_results.jsonl", results)
        scored_count = sum(result["status"] == "scored" for result in results)
        execution_failed = sum(
            result["status"] == "execution_failed" for result in results
        )
        evaluation_failed = sum(
            result["status"] == "evaluation_failed" for result in results
        )
        quality_accounted = scored_count + execution_failed
        summary = {
            "overall_attempt_pass_rate": (
                scored_count / quality_accounted if quality_accounted else None
            ),
            "passed_attempts": scored_count,
            "quality_accounted_attempts": quality_accounted,
            "macro_mean_scored_attempt_required_atom_recall": 1.0,
            "attempt_lifecycle_counts": {
                "scored": scored_count,
                "execution_failed": execution_failed,
                "evaluation_failed": evaluation_failed,
            },
            "items": [],
        }
        write_json(output_dir / "summary.json", summary)
        write_text(output_dir / "report.md", "# Browser E2E report\n")
        artifact_names = {
            "prepared_items.jsonl",
            "preparation_results.jsonl",
            "answers.jsonl",
            "evaluation_results.jsonl",
            "summary.json",
            "report.md",
        }
        manifest = {
            "schema_version": "qa-v0",
            "run_id": run_id,
            "status": "scored",
            "attempts": attempts,
            "artifacts": artifact_hashes(output_dir, artifact_names),
            "phases": {
                "prepare": {"status": "completed"},
                "run": {"status": "completed"},
                "score": {
                    "status": "completed",
                    "completed_at": "2026-07-24T12:00:00+00:00",
                },
            },
        }
        write_json(output_dir / "manifest.json", manifest)
        return manifest

    def retry_plan(self, parent_run_dir):
        manifest = read_json(parent_run_dir / "manifest.json")
        results = read_jsonl(parent_run_dir / "evaluation_results.jsonl")
        retryable = [
            result
            for result in results
            if result["status"] in {"execution_failed", "evaluation_failed"}
        ]
        if not retryable:
            raise ValueError("evaluation run has no failed attempts to retry")
        return {
            "parent_run_id": manifest["run_id"],
            "retry_attempt_ids": [result["attempt_id"] for result in retryable],
            "execution_attempt_ids": [
                result["attempt_id"]
                for result in retryable
                if result["status"] == "execution_failed"
            ],
            "evaluation_attempt_ids": [
                result["attempt_id"]
                for result in retryable
                if result["status"] == "evaluation_failed"
            ],
            "carried_forward_attempt_ids": [
                result["attempt_id"]
                for result in results
                if result["status"] == "scored"
            ],
        }

    def retry(self, parent_run_dir, output_dir):
        parent_manifest = read_json(parent_run_dir / "manifest.json")
        plan = self.retry_plan(parent_run_dir)
        prepared = read_jsonl(parent_run_dir / "prepared_items.jsonl")
        preparation = read_jsonl(parent_run_dir / "preparation_results.jsonl")
        answers = {
            row["attempt_id"]: row
            for row in read_jsonl(parent_run_dir / "answers.jsonl")
        }
        parent_results = read_jsonl(parent_run_dir / "evaluation_results.jsonl")
        prepared_by_id = {row["item_id"]: row for row in prepared}
        successor_answers = []
        successor_results = []
        retry_ids = set(plan["retry_attempt_ids"])
        for result in parent_results:
            attempt_id = result["attempt_id"]
            if attempt_id not in retry_ids:
                successor_answers.append(answers[attempt_id])
                successor_results.append(result)
                continue
            prepared_item = prepared_by_id[result["item_id"]]
            answer = {
                "item_id": result["item_id"],
                "attempt_id": attempt_id,
                "ordinal": result["ordinal"],
                "status": "answer_ready",
                "duration_ms": 325 + result["ordinal"],
                "answer": answers[attempt_id].get(
                    "answer", f"Recovered answer for {result['item_id']}"
                ),
            }
            successor_answers.append(answer)
            successor_results.append(
                {
                    "item_id": result["item_id"],
                    "attempt_id": attempt_id,
                    "ordinal": result["ordinal"],
                    "status": "scored",
                    "answer": answer["answer"],
                    "passed": True,
                    "atom_score": 1.0,
                    "required_atom_recall": 1.0,
                    "judgments": [
                        {
                            "atom_id": atom["id"],
                            "outcome": "entailed",
                            "rationale": "Deterministic retry succeeded.",
                        }
                        for atom in prepared_item["gold_atoms"]
                    ],
                }
            )
        write_jsonl(output_dir / "prepared_items.jsonl", prepared)
        write_jsonl(output_dir / "preparation_results.jsonl", preparation)
        write_jsonl(output_dir / "answers.jsonl", successor_answers)
        write_jsonl(output_dir / "evaluation_results.jsonl", successor_results)
        summary = {
            "overall_attempt_pass_rate": 1.0,
            "passed_attempts": len(successor_results),
            "quality_accounted_attempts": len(successor_results),
            "macro_mean_scored_attempt_required_atom_recall": 1.0,
            "attempt_lifecycle_counts": {
                "scored": len(successor_results),
                "execution_failed": 0,
                "evaluation_failed": 0,
            },
            "items": [],
        }
        write_json(output_dir / "summary.json", summary)
        write_text(output_dir / "report.md", "# Browser E2E retry report\n")
        artifact_names = {
            "prepared_items.jsonl",
            "preparation_results.jsonl",
            "answers.jsonl",
            "evaluation_results.jsonl",
            "summary.json",
            "report.md",
        }
        manifest = {
            "schema_version": "qa-v0",
            "run_id": "browser-e2e-retry-run",
            "status": "scored",
            "attempts": parent_manifest["attempts"],
            "artifacts": artifact_hashes(output_dir, artifact_names),
            "phases": {
                "prepare": {"status": "completed"},
                "run": {"status": "completed"},
                "score": {
                    "status": "completed",
                    "completed_at": "2026-07-24T12:01:00+00:00",
                },
            },
            "retry": plan,
        }
        write_json(output_dir / "manifest.json", manifest)
        return manifest


def create_app():
    repository = Path(__file__).resolve().parents[2]
    app = Flask(
        __name__,
        template_folder=str(repository / "src/interfaces/chat_app/templates"),
        static_folder=str(repository / "src/interfaces/chat_app/static"),
    )
    root = Path(os.environ["EVALUATION_TEST_ROOT"])
    config_path = root / "config.yaml"
    write_text(
        config_path,
        "services:\n  chat_app:\n    agent_class: FakeAgent\n"
        "    default_provider: fake\n    default_model: fake-model\n",
    )
    service = EvaluationConsoleService(
        root,
        agent_config_path=config_path,
        agents_dir=repository / "examples/agents",
        workflow_factory=FakeWorkflow,
    )

    def allow(_permission):
        return lambda view: view

    register_evaluations(app, require_perm=allow, service=service)
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=2787, debug=False)
