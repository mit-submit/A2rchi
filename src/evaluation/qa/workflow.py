# isort: skip_file
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .artifacts import (  # isort: skip
    AtomicJsonlWriter,
    artifact_hashes,
    iter_jsonl,
    read_json,
    read_jsonl,
    sha256_file,
    utc_now,
    verify_hashes,
    write_bytes,
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
from .profile import EvaluatorProfile, load_profile
from .runtime import ArchiAgentRuntime, LangChainEvaluatorRuntime, load_agent_inputs
from .scoring import build_summary, render_report, score_attempt
from .validation import (  # isort: skip
    Atom,
    load_dataset,
    validate_gold_output,
    validate_judgments,
)


class QAWorkflow:
    def __init__(
        self,
        evaluator_factory: Callable[
            [EvaluatorProfile], Any
        ] = LangChainEvaluatorRuntime,
        agent_factory: Callable[[Dict[str, Any], Any, type], Any] = ArchiAgentRuntime,
    ):
        self.evaluator_factory = evaluator_factory
        self.agent_factory = agent_factory

    @staticmethod
    def _require_positive_attempts(attempts: int) -> None:
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
            raise ValueError("attempts must be a positive integer")

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

    def prepare(
        self,
        dataset: Path,
        output_dir: Path,
        evaluator_profile_path: Optional[Path] = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        dataset_format, items, dataset_bytes = load_dataset(dataset)
        profile = load_profile(evaluator_profile_path)
        existing = self._existing_owned(output_dir, OWNED_FILES)
        if existing and not overwrite:
            raise ValueError(
                "output directory already contains QA artifacts: "
                + ", ".join(existing)
                + "; use --overwrite"
            )
        evaluator = self.evaluator_factory(profile)
        output_dir.mkdir(parents=True, exist_ok=True)
        if overwrite:
            self._remove_owned(output_dir, OWNED_FILES)
        started_at = utc_now()
        snapshot_name = f"input.snapshot.{dataset_format}"
        write_bytes(output_dir / snapshot_name, dataset_bytes)
        write_yaml(output_dir / "evaluator_profile.resolved.yaml", profile.to_dict())

        prepared_rows: List[Dict[str, Any]] = []
        result_rows: List[Dict[str, Any]] = []
        for item in items:
            metadata = {
                "answer_mode": item.answer_mode,
                "answer_source": item.answer_source,
            }
            if item.freshness == "live":
                result_rows.append(
                    {"item_id": item.id, "status": "skipped_live", **metadata}
                )
                continue
            try:
                if item.expected_atoms is not None:
                    gold_atoms = item.expected_atoms
                    atom_source = "supplied"
                else:
                    gold_atoms = validate_gold_output(
                        evaluator.extract_gold(item.question, item.expected_answer),
                        context=f"gold extraction for item {item.id}",
                    )
                    atom_source = "inferred"
            except Exception as exc:
                result_rows.append(
                    {
                        "item_id": item.id,
                        "status": "preparation_failed",
                        "error": str(exc),
                        **metadata,
                    }
                )
                continue
            prepared_rows.append(
                {
                    "item_id": item.id,
                    "question": item.question,
                    "expected_answer": item.expected_answer,
                    "freshness": item.freshness,
                    **metadata,
                    "atom_source": atom_source,
                    "gold_atoms": [atom.to_dict() for atom in gold_atoms],
                }
            )
            result_rows.append({"item_id": item.id, "status": "prepared", **metadata})

        write_jsonl(output_dir / "prepared_items.jsonl", prepared_rows)
        write_jsonl(output_dir / "preparation_results.jsonl", result_rows)
        prep_names = {
            snapshot_name,
            "prepared_items.jsonl",
            "preparation_results.jsonl",
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
                    "input_items": len(items),
                    "prepared_items": len(prepared_rows),
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
        _resolved_agent_inputs: Optional[Tuple[Dict[str, Any], Any, str, type]] = None,
    ) -> Dict[str, Any]:
        self._require_positive_attempts(attempts)
        manifest = self._load_manifest(run_dir)
        self._phase_complete(manifest, "prepare")
        snapshot = manifest["input"]["snapshot"]
        verify_hashes(
            run_dir,
            manifest,
            {
                snapshot,
                "prepared_items.jsonl",
                "preparation_results.jsonl",
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
        config, spec, spec_text, pipeline_class = (
            _resolved_agent_inputs
            if _resolved_agent_inputs is not None
            else load_agent_inputs(agent_config, agent_spec)
        )
        prepared_rows = read_jsonl(run_dir / "prepared_items.jsonl")
        if not prepared_rows:
            raise ValueError("run requires at least one prepared item")
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
        runtime = self.agent_factory(config, spec, pipeline_class)
        started_at = utc_now()
        attempt_slots = 0
        with AtomicJsonlWriter(run_dir / "answers.jsonl") as answer_writer:
            for prepared in prepared_rows:
                for ordinal in range(1, attempts + 1):
                    attempt_slots += 1
                    attempt_id = f"{prepared['item_id']}-attempt-{ordinal}"
                    base = {
                        "item_id": prepared["item_id"],
                        "attempt_id": attempt_id,
                        "ordinal": ordinal,
                        "agent_config_sha256": config_hash,
                        "agent_spec_sha256": spec_hash,
                    }
                    try:
                        answer = runtime.run(prepared["question"])
                        answer_writer.write(
                            {
                                **base,
                                "status": "answer_ready",
                                "answer": answer,
                            }
                        )
                    except Exception as exc:
                        error = {"type": type(exc).__name__, "message": str(exc)}
                        answer_writer.write(
                            {**base, "status": "execution_failed", "error": error}
                        )
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
        }
        manifest["status"] = "run_completed"
        write_json(run_dir / "manifest.json", manifest)
        return manifest

    def score(
        self,
        run_dir: Path,
        evaluator_profile_path: Optional[Path] = None,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        manifest = self._load_manifest(run_dir)
        self._phase_complete(manifest, "prepare")
        self._phase_complete(manifest, "run")
        verify_hashes(
            run_dir,
            manifest,
            {
                manifest["input"]["snapshot"],
                "prepared_items.jsonl",
                "preparation_results.jsonl",
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
        prepared_rows = read_jsonl(run_dir / "prepared_items.jsonl")
        preparation_results = read_jsonl(run_dir / "preparation_results.jsonl")
        expected_slots = len(prepared_rows) * manifest["attempts"]
        answer_count = 0
        actual_identities = set()
        has_answer_ready = False
        allowed_statuses = {"answer_ready", "execution_failed"}
        for row in iter_jsonl(run_dir / "answers.jsonl"):
            answer_count += 1
            if row.get("status") not in allowed_statuses:
                raise ValueError(
                    "run contains a non-terminal or unsupported attempt status"
                )
            if row["status"] == "answer_ready":
                has_answer_ready = True
            actual_identities.add(
                (row.get("item_id"), row.get("ordinal"), row.get("attempt_id"))
            )
        if answer_count != expected_slots:
            raise ValueError(
                f"run has {answer_count} terminal slots; expected exactly {expected_slots}"
            )
        expected_identities = {
            (prepared["item_id"], ordinal, f"{prepared['item_id']}-attempt-{ordinal}")
            for prepared in prepared_rows
            for ordinal in range(1, manifest["attempts"] + 1)
        }
        if (
            actual_identities != expected_identities
            or len(actual_identities) != answer_count
        ):
            raise ValueError(
                "run attempt slot identities do not match the prepared workspace"
            )
        evaluator = self.evaluator_factory(profile) if has_answer_ready else None
        if overwrite:
            self._remove_owned(run_dir, SCORE_FILES)
            manifest["phases"].pop("score", None)
            for name in SCORE_FILES:
                manifest["artifacts"].pop(name, None)
        prepared_by_id = {row["item_id"]: row for row in prepared_rows}
        started_at = utc_now()
        with AtomicJsonlWriter(run_dir / "evaluation_results.jsonl") as result_writer:
            for answer_row in iter_jsonl(run_dir / "answers.jsonl"):
                base = {
                    key: answer_row[key]
                    for key in (
                        "item_id",
                        "attempt_id",
                        "ordinal",
                        "agent_config_sha256",
                        "agent_spec_sha256",
                    )
                }
                if answer_row["status"] == "execution_failed":
                    result_writer.write(
                        {
                            **base,
                            "status": "execution_failed",
                            "error": answer_row["error"],
                        }
                    )
                    continue
                prepared = prepared_by_id[answer_row["item_id"]]
                gold_atoms = [Atom(**atom) for atom in prepared["gold_atoms"]]
                try:
                    assert evaluator is not None
                    judgments = validate_judgments(
                        evaluator.compare(
                            prepared["question"], gold_atoms, answer_row["answer"]
                        ),
                        gold_atoms=gold_atoms,
                        context=f"comparison for attempt {answer_row['attempt_id']}",
                    )
                    if any(judgment.outcome == "unjudgeable" for judgment in judgments):
                        raise ValueError("comparator returned an unjudgeable outcome")
                    metrics = score_attempt(gold_atoms, judgments)
                    result_writer.write(
                        {
                            **base,
                            "status": "scored",
                            "answer": answer_row["answer"],
                            "judgments": [judgment.to_dict() for judgment in judgments],
                            **metrics,
                        }
                    )
                except Exception as exc:
                    result_writer.write(
                        {
                            **base,
                            "status": "evaluation_failed",
                            "error": str(exc),
                        }
                    )

        summary = build_summary(
            preparation_results,
            prepared_rows,
            iter_jsonl(run_dir / "evaluation_results.jsonl"),
        )
        summary["provenance"] = {
            "agent_config_sha256": manifest["artifacts"][
                "agent_config.resolved.yaml"
            ],
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
        }
        manifest["artifacts"].update(
            artifact_hashes(run_dir, SCORE_FILES - {"report.md"})
        )
        write_text(run_dir / "report.md", render_report(summary, manifest))
        manifest["artifacts"]["report.md"] = sha256_file(run_dir / "report.md")
        write_json(run_dir / "manifest.json", manifest)
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
    ) -> Dict[str, Any]:
        self._require_positive_attempts(attempts)
        resolved_agent_inputs = load_agent_inputs(agent_config, agent_spec)
        self.prepare(dataset, output_dir, evaluator_profile_path, overwrite)
        self.run(
            output_dir,
            agent_config,
            agent_spec,
            attempts,
            overwrite=False,
            _resolved_agent_inputs=resolved_agent_inputs,
        )
        return self.score(output_dir, overwrite=False)
