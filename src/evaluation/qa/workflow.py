# isort: skip_file
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import local
from time import perf_counter
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from .artifacts import (  # isort: skip
    AtomicJsonlWriter,
    artifact_hashes,
    copy_file_atomic,
    iter_jsonl,
    read_json,
    read_jsonl,
    sha256_file,
    utc_now,
    verify_hashes,
    write_json,
    write_jsonl,
    write_text,
    write_yaml,
)
from .constants import (  # isort: skip
    OWNED_FILES,
    PROMPT_VERSIONS,
    RUN_FILES,
    SCHEMA_VERSION,
    SCORE_FILES,
    SCORING_VERSION,
)
from .profile import load_profile
from .preparation import (
    AnswerComparator,
    PreparationRecord,
    iter_preparation_records,
    load_preparation_records,
    prepare_dataset_item,
)
from .runtime import ArchiAgentRuntime, LangChainEvaluatorRuntime, load_agent_inputs
from .scoring import build_summary, render_report, score_attempt
from .validation import (  # isort: skip
    dataset_source_format,
    iter_dataset_items,
    validate_judgments,
)


class QAWorkflow:
    MAX_PHASE_WORKERS = 16

    @staticmethod
    def _require_positive_attempts(attempts: int) -> None:
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
            raise ValueError("attempts must be a positive integer")

    @classmethod
    def require_worker_count(cls, workers: int, field: str) -> None:
        if (
            isinstance(workers, bool)
            or not isinstance(workers, int)
            or workers < 1
            or workers > cls.MAX_PHASE_WORKERS
        ):
            raise ValueError(
                f"{field} must be an integer from 1 to {cls.MAX_PHASE_WORKERS}"
            )

    @staticmethod
    def _batches(items: Iterable[Any], size: int) -> Iterator[List[Any]]:
        iterator = iter(items)
        while True:
            batch = list(islice(iterator, size))
            if not batch:
                return
            yield batch

    @staticmethod
    def _duration_ms(started_at: float) -> int:
        return max(0, int(round((perf_counter() - started_at) * 1000)))

    @staticmethod
    def _tool_calls(runtime: Any) -> List[Dict[str, Any]]:
        return [dict(call) for call in runtime.tool_calls]

    def _run_attempt(
        self,
        runtime: Any,
        question: str,
        base: Dict[str, Any],
    ) -> Dict[str, Any]:
        attempt_started_at = perf_counter()
        try:
            answer = runtime.run(question)
        except Exception as exc:
            duration_ms = self._duration_ms(attempt_started_at)
            return {
                **base,
                "status": "execution_failed",
                "duration_ms": duration_ms,
                "tool_calls": self._tool_calls(runtime),
                "error": {"type": type(exc).__name__, "message": str(exc)},
            }
        return {
            **base,
            "status": "answer_ready",
            "duration_ms": self._duration_ms(attempt_started_at),
            "tool_calls": self._tool_calls(runtime),
            "answer": answer,
        }

    @staticmethod
    def _remove_owned(run_dir: Path, names: set) -> None:
        directories = sorted(
            name
            for name in names
            if (run_dir / name).is_dir() and not (run_dir / name).is_symlink()
        )
        if directories:
            raise ValueError(
                "QA artifact path(s) are directories: " + ", ".join(directories)
            )
        for name in names:
            path = run_dir / name
            if path.is_file() or path.is_symlink():
                path.unlink()

    @staticmethod
    def _existing_owned(run_dir: Path, names: set) -> List[str]:
        if not run_dir.exists():
            return []
        return sorted(name for name in names if (run_dir / name).exists())

    @staticmethod
    def _load_manifest(run_dir: Path) -> Dict[str, Any]:
        path = run_dir / "manifest.json"
        if not path.exists():
            raise ValueError(f"run workspace has no manifest: {run_dir}")
        manifest = read_json(path)
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != SCHEMA_VERSION
        ):
            raise ValueError("run workspace manifest has an unsupported schema")
        return manifest

    @staticmethod
    def _phase_complete(manifest: Dict[str, Any], phase: str) -> None:
        phases = manifest.get("phases")
        if (
            not isinstance(phases, dict)
            or (phases.get(phase) or {}).get("status") != "completed"
        ):
            raise ValueError(f"run workspace {phase} phase is not complete")

    @staticmethod
    def _load_preparation(
        run_dir: Path, manifest: Dict[str, Any]
    ) -> List[PreparationRecord]:
        return load_preparation_records(
            run_dir / "preparation.jsonl",
            expected_count=manifest["phases"]["prepare"]["input_items"],
        )

    @staticmethod
    def _iter_answer_pairs(
        run_dir: Path, manifest: Dict[str, Any]
    ) -> Iterator[Tuple[PreparationRecord, Dict[str, Any]]]:
        answers = iter_jsonl(run_dir / "answers.jsonl")
        allowed_statuses = {"answer_ready", "execution_failed"}
        for prepared in iter_preparation_records(
            run_dir / "preparation.jsonl",
            expected_count=manifest["phases"]["prepare"]["input_items"],
        ):
            if prepared.status != "prepared":
                continue
            for ordinal in range(1, manifest["attempts"] + 1):
                try:
                    answer = next(answers)
                except StopIteration:
                    raise ValueError(
                        "run contains fewer terminal slots than the prepared workspace"
                    ) from None
                if answer.get("status") not in allowed_statuses:
                    raise ValueError(
                        "run contains a non-terminal or unsupported attempt status"
                    )
                expected_identity = (
                    prepared.item_id,
                    ordinal,
                    f"{prepared.item_id}-attempt-{ordinal}",
                )
                actual_identity = (
                    answer.get("item_id"),
                    answer.get("ordinal"),
                    answer.get("attempt_id"),
                )
                if actual_identity != expected_identity:
                    raise ValueError(
                        "run attempt slot identities do not match the prepared workspace"
                    )
                yield prepared, answer

        try:
            next(answers)
        except StopIteration:
            return
        raise ValueError("run contains more terminal slots than the prepared workspace")

    @staticmethod
    def _attempt_base(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: row[key]
            for key in (
                "item_id",
                "attempt_id",
                "ordinal",
                "agent_config_sha256",
                "agent_spec_sha256",
            )
        }

    @staticmethod
    def _score_answer(
        answer_row: Dict[str, Any],
        prepared: PreparationRecord,
        evaluator: AnswerComparator,
    ) -> Dict[str, Any]:
        base = QAWorkflow._attempt_base(answer_row)
        gold_atoms = prepared.prepared_gold_atoms
        try:
            judgments = validate_judgments(
                evaluator.compare(
                    prepared.prepared_question, gold_atoms, answer_row["answer"]
                ),
                gold_atoms=gold_atoms,
                context=f"comparison for attempt {answer_row['attempt_id']}",
            )
            if any(judgment.outcome == "unjudgeable" for judgment in judgments):
                raise ValueError("comparator returned an unjudgeable outcome")
            metrics = score_attempt(gold_atoms, judgments)
            return {
                **base,
                "status": "scored",
                "answer": answer_row["answer"],
                "judgments": [judgment.to_dict() for judgment in judgments],
                **metrics,
            }
        except Exception as exc:
            return {
                **base,
                "status": "evaluation_failed",
                "error": str(exc),
            }

    def _load_retry_parent(self, run_dir: Path) -> Tuple[
        Dict[str, Any],
        List[PreparationRecord],
        Dict[str, Dict[str, Any]],
        List[Dict[str, Any]],
    ]:
        manifest = self._load_manifest(run_dir)
        if manifest.get("status") != "scored":
            raise ValueError("evaluation retry requires a complete scored run")
        for phase in ("prepare", "run", "score"):
            self._phase_complete(manifest, phase)
        if manifest.get("versions") != {
            "scoring": SCORING_VERSION,
            "prompts": PROMPT_VERSIONS,
        }:
            raise ValueError("evaluation retry requires current QA artifact versions")
        snapshot = manifest["input"]["snapshot"]
        required_artifacts = {
            snapshot,
            "preparation.jsonl",
            "evaluator_profile.resolved.yaml",
            "agent_config.resolved.yaml",
            "agent_spec.resolved.md",
            "answers.jsonl",
            "evaluation_results.jsonl",
            "summary.json",
            "report.md",
        }
        verify_hashes(run_dir, manifest, required_artifacts)
        preparation = self._load_preparation(run_dir, manifest)
        prepared_records = [
            record for record in preparation if record.status == "prepared"
        ]
        answers = read_jsonl(run_dir / "answers.jsonl")
        results = read_jsonl(run_dir / "evaluation_results.jsonl")
        attempts = manifest.get("attempts")
        self._require_positive_attempts(attempts)
        expected_identities = {
            (
                prepared.item_id,
                ordinal,
                f"{prepared.item_id}-attempt-{ordinal}",
            )
            for prepared in prepared_records
            for ordinal in range(1, attempts + 1)
        }
        answer_identities = {
            (row.get("item_id"), row.get("ordinal"), row.get("attempt_id"))
            for row in answers
        }
        result_identities = {
            (row.get("item_id"), row.get("ordinal"), row.get("attempt_id"))
            for row in results
        }
        if (
            len(answers) != len(expected_identities)
            or len(results) != len(expected_identities)
            or answer_identities != expected_identities
            or result_identities != expected_identities
        ):
            raise ValueError(
                "parent run attempt identities do not match the prepared workspace"
            )
        answers_by_id = {row["attempt_id"]: row for row in answers}
        if len(answers_by_id) != len(answers):
            raise ValueError("parent run contains duplicate attempt answers")
        config_hash = manifest["artifacts"]["agent_config.resolved.yaml"]
        spec_hash = manifest["artifacts"]["agent_spec.resolved.md"]
        for result in results:
            answer = answers_by_id[result["attempt_id"]]
            status = result.get("status")
            if status not in {"scored", "execution_failed", "evaluation_failed"}:
                raise ValueError("parent run contains an unsupported result status")
            expected_answer_status = (
                "execution_failed" if status == "execution_failed" else "answer_ready"
            )
            if answer.get("status") != expected_answer_status:
                raise ValueError(
                    "parent run answer and evaluation result statuses disagree"
                )
            for row in (answer, result):
                if (
                    row.get("agent_config_sha256") != config_hash
                    or row.get("agent_spec_sha256") != spec_hash
                ):
                    raise ValueError("parent run attempt provenance is inconsistent")
        return (
            manifest,
            preparation,
            answers_by_id,
            results,
        )

    @staticmethod
    def _retry_plan(
        manifest: Dict[str, Any], results: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        retryable = [
            row
            for row in results
            if row["status"] in {"execution_failed", "evaluation_failed"}
        ]
        if not retryable:
            raise ValueError("evaluation run has no failed attempts to retry")
        return {
            "parent_run_id": manifest["run_id"],
            "retry_attempt_ids": [row["attempt_id"] for row in retryable],
            "execution_attempt_ids": [
                row["attempt_id"]
                for row in retryable
                if row["status"] == "execution_failed"
            ],
            "evaluation_attempt_ids": [
                row["attempt_id"]
                for row in retryable
                if row["status"] == "evaluation_failed"
            ],
            "carried_forward_attempt_ids": [
                row["attempt_id"] for row in results if row["status"] == "scored"
            ],
        }

    def retry_plan(self, run_dir: Path) -> Dict[str, Any]:
        manifest, _preparation, _answers, results = self._load_retry_parent(run_dir)
        return self._retry_plan(manifest, results)

    def prepare(
        self,
        dataset: Path,
        output_dir: Path,
        evaluator_profile_path: Optional[Path] = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        dataset_format = dataset_source_format(dataset)
        profile = load_profile(evaluator_profile_path)
        existing = self._existing_owned(output_dir, OWNED_FILES)
        if existing and not overwrite:
            raise ValueError(
                "output directory already contains QA artifacts: "
                + ", ".join(existing)
                + "; use --overwrite"
            )
        started_at = utc_now()
        snapshot_name = f"input.snapshot.{dataset_format}"
        snapshot_path = output_dir / snapshot_name
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix=f".{output_dir.name}-prepare-", dir=str(output_dir.parent)
        ) as staging_dir:
            staged_snapshot = Path(staging_dir) / snapshot_name
            copy_file_atomic(dataset, staged_snapshot)
            snapshot_item_count = sum(
                1 for _item in iter_dataset_items(staged_snapshot)
            )
            evaluator = LangChainEvaluatorRuntime(profile)
            output_dir.mkdir(parents=True, exist_ok=True)
            if overwrite:
                self._remove_owned(output_dir, OWNED_FILES)
            staged_snapshot.replace(snapshot_path)
        write_yaml(output_dir / "evaluator_profile.resolved.yaml", profile.to_dict())

        prepared_item_count = 0
        with AtomicJsonlWriter(output_dir / "preparation.jsonl") as preparation_writer:
            for item in iter_dataset_items(snapshot_path):
                record = prepare_dataset_item(item, evaluator)
                preparation_writer.write(record.to_dict())
                prepared_item_count += record.status == "prepared"
        prep_names = {
            snapshot_name,
            "preparation.jsonl",
            "evaluator_profile.resolved.yaml",
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": str(uuid.uuid4()),
            "status": "prepared",
            "versions": {
                "scoring": SCORING_VERSION,
                "prompts": PROMPT_VERSIONS,
            },
            "input": {
                "source_path": str(dataset.resolve()),
                "snapshot": snapshot_name,
            },
            "evaluator_profile": {
                "artifact": "evaluator_profile.resolved.yaml",
            },
            "artifacts": artifact_hashes(output_dir, prep_names),
            "phases": {
                "prepare": {
                    "status": "completed",
                    "started_at": started_at,
                    "completed_at": utc_now(),
                    "input_items": snapshot_item_count,
                    "prepared_items": prepared_item_count,
                }
            },
        }
        write_json(output_dir / "manifest.json", manifest)
        return manifest

    def run(
        self,
        run_dir: Path,
        agent_config: Path,
        agent_spec: Path,
        attempts: int = 1,
        overwrite: bool = False,
        run_workers: int = 1,
        _resolved_agent_inputs: Optional[Tuple[Dict[str, Any], Any, str, type]] = None,
    ) -> Dict[str, Any]:
        self._require_positive_attempts(attempts)
        self.require_worker_count(run_workers, "run_workers")
        manifest = self._load_manifest(run_dir)
        self._phase_complete(manifest, "prepare")
        snapshot = manifest["input"]["snapshot"]
        verify_hashes(
            run_dir,
            manifest,
            {
                snapshot,
                "preparation.jsonl",
                "evaluator_profile.resolved.yaml",
            },
        )
        existing = self._existing_owned(run_dir, RUN_FILES | SCORE_FILES)
        if existing and not overwrite:
            raise ValueError(
                "run or downstream artifacts already exist: "
                + ", ".join(existing)
                + "; use --overwrite"
            )
        preparation_path = run_dir / "preparation.jsonl"
        preparation_count = manifest["phases"]["prepare"]["input_items"]
        prepared_item_count = sum(
            record.status == "prepared"
            for record in iter_preparation_records(
                preparation_path,
                expected_count=preparation_count,
            )
        )
        if prepared_item_count == 0:
            raise ValueError("run requires at least one prepared item")
        config, spec, spec_text, pipeline_class = (
            _resolved_agent_inputs
            if _resolved_agent_inputs is not None
            else load_agent_inputs(agent_config, agent_spec)
        )
        if overwrite:
            self._remove_owned(run_dir, RUN_FILES | SCORE_FILES)
            manifest["phases"].pop("run", None)
            manifest["phases"].pop("score", None)
            manifest.pop("attempts", None)
            manifest.pop("agent", None)
            for name in RUN_FILES | SCORE_FILES:
                manifest["artifacts"].pop(name, None)
        write_yaml(run_dir / "agent_config.resolved.yaml", config)
        write_text(run_dir / "agent_spec.resolved.md", spec_text)
        config_hash = sha256_file(run_dir / "agent_config.resolved.yaml")
        spec_hash = sha256_file(run_dir / "agent_spec.resolved.md")
        chat = config["services"]["chat_app"]
        started_at = utc_now()
        attempt_slots = 0

        def tasks() -> Iterator[Tuple[PreparationRecord, int]]:
            for prepared in iter_preparation_records(
                preparation_path,
                expected_count=preparation_count,
            ):
                if prepared.status != "prepared":
                    continue
                for ordinal in range(1, attempts + 1):
                    yield prepared, ordinal

        def execute_attempt(
            runtime: Any, prepared: PreparationRecord, ordinal: int
        ) -> Dict[str, Any]:
            return self._run_attempt(
                runtime,
                prepared.prepared_question,
                {
                    "item_id": prepared.item_id,
                    "attempt_id": f"{prepared.item_id}-attempt-{ordinal}",
                    "ordinal": ordinal,
                    "agent_config_sha256": config_hash,
                    "agent_spec_sha256": spec_hash,
                },
            )

        with AtomicJsonlWriter(run_dir / "answers.jsonl") as answer_writer:
            if run_workers == 1:
                runtime = ArchiAgentRuntime(config, spec, pipeline_class)
                for prepared, ordinal in tasks():
                    attempt_slots += 1
                    answer_writer.write(execute_attempt(runtime, prepared, ordinal))
            else:
                worker_state = local()

                def execute_parallel(
                    task: Tuple[PreparationRecord, int],
                ) -> Dict[str, Any]:
                    runtime = worker_state.__dict__.get("runtime")
                    if runtime is None:
                        runtime = ArchiAgentRuntime(config, spec, pipeline_class)
                        worker_state.runtime = runtime
                    return execute_attempt(runtime, *task)

                with ThreadPoolExecutor(
                    max_workers=run_workers,
                    thread_name_prefix="archi-qa-run",
                ) as executor:
                    for batch in self._batches(tasks(), run_workers * 2):
                        for answer_row in executor.map(execute_parallel, batch):
                            attempt_slots += 1
                            answer_writer.write(answer_row)
        manifest["attempts"] = attempts
        manifest["agent"] = {
            "agent_class": chat["agent_class"],
            "provider": chat["default_provider"],
            "model": chat["default_model"],
            "config_artifact": "agent_config.resolved.yaml",
            "spec_artifact": "agent_spec.resolved.md",
        }
        manifest["artifacts"].update(artifact_hashes(run_dir, RUN_FILES))
        manifest["phases"]["run"] = {
            "status": "completed",
            "started_at": started_at,
            "completed_at": utc_now(),
            "attempt_slots": attempt_slots,
            "workers": run_workers,
        }
        manifest["status"] = "run_completed"
        write_json(run_dir / "manifest.json", manifest)
        return manifest

    def score(
        self,
        run_dir: Path,
        evaluator_profile_path: Optional[Path] = None,
        overwrite: bool = False,
        score_workers: int = 1,
    ) -> Dict[str, Any]:
        self.require_worker_count(score_workers, "score_workers")
        manifest = self._load_manifest(run_dir)
        self._phase_complete(manifest, "prepare")
        self._phase_complete(manifest, "run")
        verify_hashes(
            run_dir,
            manifest,
            {
                manifest["input"]["snapshot"],
                "preparation.jsonl",
                "evaluator_profile.resolved.yaml",
                "agent_config.resolved.yaml",
                "agent_spec.resolved.md",
                "answers.jsonl",
            },
        )
        existing = self._existing_owned(run_dir, SCORE_FILES)
        if existing and not overwrite:
            raise ValueError(
                "score phase artifacts already exist: "
                + ", ".join(existing)
                + "; use --overwrite"
            )
        stored_profile = load_profile(run_dir / "evaluator_profile.resolved.yaml")
        if evaluator_profile_path is not None:
            supplied = load_profile(evaluator_profile_path)
            if supplied != stored_profile:
                raise ValueError(
                    "supplied evaluator profile does not match the prepared profile"
                )
        profile = stored_profile
        has_answer_ready = False
        for _prepared, answer in self._iter_answer_pairs(run_dir, manifest):
            if answer["status"] == "answer_ready":
                has_answer_ready = True
        if overwrite:
            self._remove_owned(run_dir, SCORE_FILES)
            manifest["phases"].pop("score", None)
            for name in SCORE_FILES:
                manifest["artifacts"].pop(name, None)
        started_at = utc_now()

        def score_pair(
            pair: Tuple[PreparationRecord, Dict[str, Any]],
            evaluator: Optional[AnswerComparator],
        ) -> Dict[str, Any]:
            prepared, answer_row = pair
            base = self._attempt_base(answer_row)
            if answer_row["status"] == "execution_failed":
                return {
                    **base,
                    "status": "execution_failed",
                    "error": answer_row["error"],
                }
            assert evaluator is not None
            return self._score_answer(answer_row, prepared, evaluator)

        with AtomicJsonlWriter(run_dir / "evaluation_results.jsonl") as result_writer:
            if score_workers == 1:
                evaluator = (
                    LangChainEvaluatorRuntime(profile) if has_answer_ready else None
                )
                for pair in self._iter_answer_pairs(run_dir, manifest):
                    result_writer.write(score_pair(pair, evaluator))
            else:
                worker_state = local()

                def score_parallel(
                    pair: Tuple[PreparationRecord, Dict[str, Any]],
                ) -> Dict[str, Any]:
                    if pair[1]["status"] == "execution_failed":
                        return score_pair(pair, None)
                    evaluator = worker_state.__dict__.get("evaluator")
                    if evaluator is None:
                        evaluator = LangChainEvaluatorRuntime(profile)
                        worker_state.evaluator = evaluator
                    return score_pair(pair, evaluator)

                with ThreadPoolExecutor(
                    max_workers=score_workers,
                    thread_name_prefix="archi-qa-score",
                ) as executor:
                    pairs = self._iter_answer_pairs(run_dir, manifest)
                    for batch in self._batches(pairs, score_workers * 2):
                        for result in executor.map(score_parallel, batch):
                            result_writer.write(result)

        preparation = self._load_preparation(run_dir, manifest)
        summary = build_summary(
            preparation,
            iter_jsonl(run_dir / "evaluation_results.jsonl"),
        )
        summary["provenance"] = {
            "agent_config_sha256": manifest["artifacts"]["agent_config.resolved.yaml"],
            "agent_spec_sha256": manifest["artifacts"]["agent_spec.resolved.md"],
            "evaluator_profile_sha256": manifest["artifacts"][
                "evaluator_profile.resolved.yaml"
            ],
        }
        write_json(run_dir / "summary.json", summary)
        manifest["status"] = "scored"
        manifest["phases"]["score"] = {
            "status": "completed",
            "started_at": started_at,
            "completed_at": utc_now(),
            "scored_attempts": summary["attempt_lifecycle_counts"]["scored"],
            "workers": score_workers,
        }
        manifest["artifacts"].update(
            artifact_hashes(run_dir, SCORE_FILES - {"report.md"})
        )
        write_text(run_dir / "report.md", render_report(summary, manifest))
        manifest["artifacts"]["report.md"] = sha256_file(run_dir / "report.md")
        write_json(run_dir / "manifest.json", manifest)
        return manifest

    def retry(self, parent_run_dir: Path, output_dir: Path) -> Dict[str, Any]:
        (
            parent_manifest,
            preparation,
            parent_answers,
            parent_results,
        ) = self._load_retry_parent(parent_run_dir)
        plan = self._retry_plan(parent_manifest, parent_results)
        run_workers = parent_manifest["phases"]["run"].get("workers", 1)
        score_workers = parent_manifest["phases"]["score"].get("workers", 1)
        self.require_worker_count(run_workers, "run_workers")
        self.require_worker_count(score_workers, "score_workers")
        existing = self._existing_owned(output_dir, OWNED_FILES)
        if existing:
            raise ValueError(
                "retry output directory already contains QA artifacts: "
                + ", ".join(existing)
            )
        retry_ids = set(plan["retry_attempt_ids"])
        execution_ids = set(plan["execution_attempt_ids"])
        prepared_by_id = {
            record.item_id: record
            for record in preparation
            if record.status == "prepared"
        }
        if execution_ids:
            config, spec, _spec_text, pipeline_class = load_agent_inputs(
                parent_run_dir / "agent_config.resolved.yaml",
                parent_run_dir / "agent_spec.resolved.md",
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        snapshot = parent_manifest["input"]["snapshot"]
        copied_artifacts = {
            snapshot,
            "preparation.jsonl",
            "evaluator_profile.resolved.yaml",
            "agent_config.resolved.yaml",
            "agent_spec.resolved.md",
        }
        for name in copied_artifacts:
            copy_file_atomic(parent_run_dir / name, output_dir / name)
        manifest = {
            "schema_version": parent_manifest["schema_version"],
            "run_id": str(uuid.uuid4()),
            "status": "prepared",
            "versions": deepcopy(parent_manifest["versions"]),
            "input": deepcopy(parent_manifest["input"]),
            "evaluator_profile": deepcopy(parent_manifest["evaluator_profile"]),
            "attempts": parent_manifest["attempts"],
            "agent": deepcopy(parent_manifest["agent"]),
            "artifacts": artifact_hashes(
                output_dir,
                {
                    snapshot,
                    "preparation.jsonl",
                    "evaluator_profile.resolved.yaml",
                },
            ),
            "phases": {
                "prepare": {
                    **deepcopy(parent_manifest["phases"]["prepare"]),
                    "source": "carried_forward",
                }
            },
            "retry": plan,
        }
        write_json(output_dir / "manifest.json", manifest)

        started_at = utc_now()
        execution_tasks = [
            result for result in parent_results if result["attempt_id"] in execution_ids
        ]

        def retry_execution(
            runtime: Any, parent_result: Dict[str, Any]
        ) -> Dict[str, Any]:
            parent_answer = parent_answers[parent_result["attempt_id"]]
            prepared = prepared_by_id[parent_result["item_id"]]
            return self._run_attempt(
                runtime,
                prepared.prepared_question,
                self._attempt_base(parent_answer),
            )

        retried_answers: Dict[str, Dict[str, Any]] = {}
        if execution_tasks and run_workers == 1:
            runtime = ArchiAgentRuntime(config, spec, pipeline_class)
            for task in execution_tasks:
                retried_answers[task["attempt_id"]] = retry_execution(runtime, task)
        elif execution_tasks:
            worker_state = local()

            def retry_execution_parallel(task: Dict[str, Any]) -> Dict[str, Any]:
                runtime = worker_state.__dict__.get("runtime")
                if runtime is None:
                    runtime = ArchiAgentRuntime(config, spec, pipeline_class)
                    worker_state.runtime = runtime
                return retry_execution(runtime, task)

            with ThreadPoolExecutor(
                max_workers=run_workers,
                thread_name_prefix="archi-qa-retry-run",
            ) as executor:
                for batch in self._batches(execution_tasks, run_workers * 2):
                    for answer_row in executor.map(retry_execution_parallel, batch):
                        retried_answers[answer_row["attempt_id"]] = answer_row

        successor_answers = [
            retried_answers.get(
                result["attempt_id"], parent_answers[result["attempt_id"]]
            )
            for result in parent_results
        ]
        write_jsonl(output_dir / "answers.jsonl", successor_answers)
        manifest["artifacts"].update(artifact_hashes(output_dir, RUN_FILES))
        manifest["phases"]["run"] = {
            "status": "completed",
            "started_at": started_at,
            "completed_at": utc_now(),
            "attempt_slots": len(successor_answers),
            "retried_attempts": len(execution_ids),
            "workers": run_workers,
        }
        manifest["status"] = "run_completed"
        write_json(output_dir / "manifest.json", manifest)

        profile = load_profile(output_dir / "evaluator_profile.resolved.yaml")
        successor_answers_by_id = {row["attempt_id"]: row for row in successor_answers}
        started_at = utc_now()
        score_tasks = [
            result for result in parent_results if result["attempt_id"] in retry_ids
        ]

        def retry_score(
            evaluator: Optional[AnswerComparator], parent_result: Dict[str, Any]
        ) -> Dict[str, Any]:
            answer_row = successor_answers_by_id[parent_result["attempt_id"]]
            if answer_row["status"] == "execution_failed":
                return {
                    **self._attempt_base(answer_row),
                    "status": "execution_failed",
                    "error": answer_row["error"],
                }
            assert evaluator is not None
            return self._score_answer(
                answer_row,
                prepared_by_id[answer_row["item_id"]],
                evaluator,
            )

        retried_results: Dict[str, Dict[str, Any]] = {}
        if score_tasks and score_workers == 1:
            evaluator = None
            for task in score_tasks:
                answer_row = successor_answers_by_id[task["attempt_id"]]
                if answer_row["status"] != "execution_failed" and evaluator is None:
                    evaluator = LangChainEvaluatorRuntime(profile)
                result = retry_score(evaluator, task)
                retried_results[result["attempt_id"]] = result
        elif score_tasks:
            worker_state = local()

            def retry_score_parallel(task: Dict[str, Any]) -> Dict[str, Any]:
                answer_row = successor_answers_by_id[task["attempt_id"]]
                if answer_row["status"] == "execution_failed":
                    return retry_score(None, task)
                evaluator = worker_state.__dict__.get("evaluator")
                if evaluator is None:
                    evaluator = LangChainEvaluatorRuntime(profile)
                    worker_state.evaluator = evaluator
                return retry_score(evaluator, task)

            with ThreadPoolExecutor(
                max_workers=score_workers,
                thread_name_prefix="archi-qa-retry-score",
            ) as executor:
                for batch in self._batches(score_tasks, score_workers * 2):
                    for result in executor.map(retry_score_parallel, batch):
                        retried_results[result["attempt_id"]] = result

        successor_results = [
            retried_results.get(result["attempt_id"], result)
            for result in parent_results
        ]
        write_jsonl(output_dir / "evaluation_results.jsonl", successor_results)
        summary = build_summary(
            preparation,
            successor_results,
        )
        summary["provenance"] = {
            "agent_config_sha256": manifest["artifacts"]["agent_config.resolved.yaml"],
            "agent_spec_sha256": manifest["artifacts"]["agent_spec.resolved.md"],
            "evaluator_profile_sha256": manifest["artifacts"][
                "evaluator_profile.resolved.yaml"
            ],
        }
        write_json(output_dir / "summary.json", summary)
        manifest["status"] = "scored"
        manifest["phases"]["score"] = {
            "status": "completed",
            "started_at": started_at,
            "completed_at": utc_now(),
            "scored_attempts": summary["attempt_lifecycle_counts"]["scored"],
            "retried_attempts": len(retry_ids),
            "workers": score_workers,
        }
        manifest["artifacts"].update(
            artifact_hashes(output_dir, SCORE_FILES - {"report.md"})
        )
        write_text(output_dir / "report.md", render_report(summary, manifest))
        manifest["artifacts"]["report.md"] = sha256_file(output_dir / "report.md")
        write_json(output_dir / "manifest.json", manifest)
        return manifest

    def composite(
        self,
        dataset: Path,
        agent_config: Path,
        agent_spec: Path,
        output_dir: Path,
        evaluator_profile_path: Optional[Path] = None,
        attempts: int = 1,
        overwrite: bool = False,
        run_workers: int = 1,
        score_workers: int = 1,
    ) -> Dict[str, Any]:
        self._require_positive_attempts(attempts)
        self.require_worker_count(run_workers, "run_workers")
        self.require_worker_count(score_workers, "score_workers")
        resolved_agent_inputs = load_agent_inputs(agent_config, agent_spec)
        self.prepare(dataset, output_dir, evaluator_profile_path, overwrite)
        self.run(
            output_dir,
            agent_config,
            agent_spec,
            attempts,
            overwrite=False,
            run_workers=run_workers,
            _resolved_agent_inputs=resolved_agent_inputs,
        )
        return self.score(
            output_dir,
            overwrite=False,
            score_workers=score_workers,
        )
