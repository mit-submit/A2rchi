# isort: skip_file
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.archi.pipelines.agents import agent_spec as agent_spec_utils

from .artifacts import read_json, sha256_file, utc_now, write_json
from .catalog import MAX_IMPORT_BYTES, DatasetRole, EvaluationCatalog, dataset_role
from .history import EvaluationHistory
from .jobs import EvaluationJobManager
from .live_checks import iter_precheck_decisions
from .oracle import OracleResolver
from .oracle_config import EvaluatorMCPRegistry
from .profile import load_profile
from .preparation import iter_preparation_records
from .runtime import LangChainEvaluatorRuntime
from .schema import CanceledRunRecord, ConsoleMetadata
from .workflow import QAWorkflow

MAX_AGENT_SNAPSHOT_BYTES = 256 * 1024


class EvaluationConsoleService:
    """Application service joining catalogs, jobs, history, and QAWorkflow."""

    def __init__(
        self,
        root: Path,
        *,
        agent_config_path: Path,
        agents_dir: Path,
        mcp_config_path: Optional[Path] = None,
    ):
        self.catalog = EvaluationCatalog(root)
        self.history = EvaluationHistory(self.catalog.runs_dir)
        self.jobs = EvaluationJobManager(self.catalog.jobs_dir)
        self.agent_config_path = Path(agent_config_path)
        self.agents_dir = Path(agents_dir)
        self.mcp_config_path = mcp_config_path

    def list_agents(self) -> List[Dict[str, str]]:
        return [
            {"id": path.name, "name": path.stem}
            for path in agent_spec_utils.list_agent_files(self.agents_dir)
        ]

    def get_job(
        self,
        job_id: str,
        *,
        include_run: bool = False,
        include_hidden: bool = False,
        include_detail: bool = False,
    ) -> Dict[str, Any]:
        job = self.jobs.get(job_id)
        result = job.get("result") or {}
        draft_id = result.get("draft_id")
        if job.get("kind") == "generate_atoms" and draft_id:
            try:
                draft = self.catalog.get_atom_draft_header(draft_id)
            except LookupError:
                pass
            else:
                job["result"] = dict(result)
                job["result"]["draft_status"] = draft.get("status")
        workspace_id = (job.get("context") or {}).get("workspace_id")
        if job.get("kind") == "evaluation" and workspace_id:
            manifest_path = self.catalog.runs_dir / workspace_id / "manifest.json"
            if manifest_path.is_file():
                try:
                    manifest = read_json(manifest_path)
                    job["progress"] = {
                        "status": manifest.get("status"),
                        "phases": manifest.get("phases") or {},
                    }
                except ValueError:
                    pass
        if job.get("status") == "attention_required" and not (
            include_run or include_hidden
        ):
            result = dict(job.get("result") or {})
            result.pop("attention_required", None)
            job["result"] = result
        if (
            (include_run or include_hidden)
            and include_detail
            and job.get("status") == "attention_required"
            and workspace_id
        ):
            run_dir = self.catalog.runs_dir / workspace_id
            try:
                manifest = read_json(run_dir / "manifest.json")
                attention = dict((job.get("result") or {})["attention_required"])
                affected = []
                serialized_bytes = 0
                for record, check, validation in iter_precheck_decisions(
                    run_dir / "preparation.jsonl",
                    run_dir / "live_checks.jsonl",
                    expected_preparation_count=manifest["phases"]["prepare"][
                        "input_items"
                    ],
                ):
                    if validation is None:
                        continue
                    row = {
                        "item_id": record.item_id,
                        "phase": validation.phase.value,
                        "reason": validation.reason.value,
                    }
                    if include_hidden:
                        row.update(
                            {
                                "question": record.question,
                                "approved_answer": record.prepared_answer,
                                "current_answer": check.answer,
                                "metadata": check.metadata,
                                "calls": [call.to_dict() for call in check.calls],
                                "oracle": record.oracle.to_dict(),
                            }
                        )
                    serialized_bytes += len(
                        json.dumps(
                            row,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ).encode("utf-8")
                    )
                    if serialized_bytes > MAX_IMPORT_BYTES:
                        raise ValueError("attention detail exceeds the 25 MB limit")
                    affected.append(row)
                attention["affected_items"] = affected
                job["result"] = dict(job["result"])
                job["result"]["attention_required"] = attention
            except KeyError:
                pass
        return job

    def list_jobs(self, *, include_run: bool = False) -> List[Dict[str, Any]]:
        return [
            self.get_job(job["id"], include_run=include_run) for job in self.jobs.list()
        ]

    def get_job_detail(
        self, job_id: str, *, include_run: bool, include_hidden: bool
    ) -> Dict[str, Any]:
        return self.get_job(
            job_id,
            include_run=include_run,
            include_hidden=include_hidden,
            include_detail=True,
        )

    def list_datasets(self) -> List[Dict[str, Any]]:
        """Return catalog summaries with a non-authoritative live-check projection."""
        datasets = self.catalog.list_datasets()
        latest_job_by_dataset: Dict[str, Dict[str, Any]] = {}
        for job in self.jobs.list():
            if job.get("kind") != "evaluation":
                continue
            dataset_id = (job.get("context") or {}).get("dataset_id")
            if isinstance(dataset_id, str) and dataset_id not in latest_job_by_dataset:
                latest_job_by_dataset[dataset_id] = job

        for dataset in datasets:
            if not dataset.get("contains_live_answers"):
                continue
            projection = {"status": "not_checked", "checked_at": None}
            job = latest_job_by_dataset.get(dataset["id"])
            if job is not None:
                workspace_id = (job.get("context") or {}).get("workspace_id")
                if not isinstance(workspace_id, str):
                    job = None
                else:
                    run_dir = self.catalog.runs_dir / workspace_id
                    manifest_path = run_dir / "manifest.json"
                    if not manifest_path.is_file():
                        job = None
            if job is not None:
                try:
                    manifest = read_json(manifest_path)
                    run_phase = manifest.get("phases", {}).get("run") or {}
                    checked_at = (
                        (manifest.get("attention_required") or {}).get("checked_at")
                        or run_phase.get("checked_at")
                        or run_phase.get("started_at")
                    )
                except (KeyError, ValueError):
                    pass
                else:
                    status = run_phase.get("live_check_status")
                    if status in {
                        "last_check_unavailable",
                        "change_observed",
                        "matched_baseline",
                    }:
                        projection = {"status": status, "checked_at": checked_at}
            dataset["last_live_check"] = projection
        return datasets

    def _agent_path(self, agent_filename: str) -> Path:
        if (
            not isinstance(agent_filename, str)
            or Path(agent_filename).name != agent_filename
            or Path(agent_filename).suffix.lower() != ".md"
        ):
            raise ValueError("agent_spec must be a catalog agent filename")
        available = {
            path.name: path
            for path in agent_spec_utils.list_agent_files(self.agents_dir)
        }
        try:
            path = available[agent_filename].resolve()
        except KeyError as exc:
            raise ValueError("selected agent spec does not exist") from exc
        try:
            path.relative_to(self.agents_dir.resolve())
        except ValueError as exc:
            raise ValueError(
                "selected agent spec is outside the configured agents directory"
            ) from exc
        return path

    def get_agent_snapshot(self, agent_filename: str) -> Dict[str, Any]:
        path = self._agent_path(agent_filename)
        with path.open("rb") as handle:
            blob = handle.read(MAX_AGENT_SNAPSHOT_BYTES + 1)
        if len(blob) > MAX_AGENT_SNAPSHOT_BYTES:
            raise ValueError("agent spec snapshot exceeds the 256 KiB limit")
        try:
            content = blob.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("agent spec must be UTF-8 text") from exc
        try:
            spec = agent_spec_utils.load_agent_spec_from_text(content)
        except agent_spec_utils.AgentSpecError as exc:
            raise ValueError(str(exc).replace("<memory>", agent_filename)) from exc
        return {
            "id": agent_filename,
            "name": spec.name,
            "tools": list(spec.tools),
            "content": content,
        }

    def start_atom_generation(
        self, dataset_id: str, profile_id: str, *, static_only: bool = False
    ) -> Dict[str, Any]:
        dataset = self.catalog.get_dataset(dataset_id)
        if (
            dataset_role(dataset) is not DatasetRole.DEFINITION_PARENT
            and dataset["atom_count"] != 0
        ):
            raise ValueError(
                "atom generation requires a dataset with zero atoms; review its "
                "existing atoms instead"
            )
        profile_path = self.catalog.profile_path(profile_id)

        def work() -> Dict[str, Any]:
            evaluator = LangChainEvaluatorRuntime(load_profile(profile_path))
            resolver = OracleResolver(EvaluatorMCPRegistry.load(self.mcp_config_path))
            draft = self.catalog.create_atom_draft(
                dataset_id,
                profile_id,
                evaluator,
                resolver,
                static_only=static_only,
                include_items=False,
            )
            return {"draft_id": draft["id"]}

        return self.jobs.start(
            "generate_atoms",
            work,
            context={
                "dataset_id": dataset_id,
                "profile_id": profile_id,
                "static_only": static_only,
            },
        )

    def start_atom_retry(self, draft_id: str) -> Dict[str, Any]:
        details = self.catalog.atom_retry_details(draft_id)
        profile_path = self.catalog.profile_path(details["profile_id"])

        def work() -> Dict[str, Any]:
            evaluator = LangChainEvaluatorRuntime(load_profile(profile_path))
            draft = self.catalog.retry_failed_atom_items(
                draft_id,
                evaluator,
                OracleResolver(EvaluatorMCPRegistry.load(self.mcp_config_path)),
                include_items=False,
            )
            return {
                "draft_id": draft["id"],
                "retried_item_ids": details["item_ids"],
            }

        return self.jobs.start(
            "generate_atoms",
            work,
            context={
                "dataset_id": details["dataset_id"],
                "draft_id": details["draft_id"],
                "profile_id": details["profile_id"],
                "retry": True,
            },
        )

    def start_live_refresh(
        self, child_dataset_id: str, profile_id: str
    ) -> Dict[str, Any]:
        self.catalog.get_dataset(child_dataset_id)
        profile_path = self.catalog.profile_path(profile_id)

        def work() -> Dict[str, Any]:
            evaluator = LangChainEvaluatorRuntime(load_profile(profile_path))
            draft = self.catalog.create_refresh_draft(
                child_dataset_id,
                profile_id,
                evaluator,
                OracleResolver(EvaluatorMCPRegistry.load(self.mcp_config_path)),
                include_items=False,
            )
            return {"draft_id": draft["id"]}

        return self.jobs.start(
            "generate_atoms",
            work,
            context={
                "dataset_id": child_dataset_id,
                "profile_id": profile_id,
                "refresh": True,
            },
        )

    def start_evaluation(
        self,
        *,
        name: str,
        dataset_id: str,
        profile_id: str,
        agent_spec: str,
        attempts: int,
        run_workers: int = 1,
        score_workers: int = 1,
    ) -> Dict[str, Any]:
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 160:
            raise ValueError("evaluation name must be a non-empty string")
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts <= 0:
            raise ValueError("attempts must be a positive integer")
        QAWorkflow.require_worker_count(run_workers, "run_workers")
        QAWorkflow.require_worker_count(score_workers, "score_workers")
        dataset = self.catalog.get_dataset(dataset_id)
        profile = self.catalog.get_profile(profile_id)
        dataset_path = self.catalog.dataset_path(dataset_id)
        if sha256_file(dataset_path) != dataset["sha256"]:
            raise ValueError("selected dataset source hash does not match its manifest")
        profile_path = self.catalog.profile_path(profile_id)
        agent_path = self._agent_path(agent_spec)
        if not self.agent_config_path.is_file():
            raise ValueError("configured evaluation agent config does not exist")
        if (
            dataset_role(dataset) is DatasetRole.DEFINITION_PARENT
            and dataset.get("time_sensitive_item_count", 0) > 0
            and dataset.get("generation_scope") is None
        ):
            raise ValueError(
                "live evaluation requires an approved internal child dataset"
            )

        run_dir = self.catalog.runs_dir / str(uuid.uuid4())
        metadata = {
            "name": name.strip(),
            "dataset_id": dataset_id,
            "dataset_name": dataset["name"],
            "profile_id": profile_id,
            "profile_name": profile["name"],
            "agent_spec": agent_spec,
            "attempts": attempts,
            "run_workers": run_workers,
            "score_workers": score_workers,
            "created_at": utc_now(),
        }

        metadata_path = run_dir / "console_metadata.json"
        run_dir.mkdir()
        try:
            write_json(metadata_path, metadata)
            return self.jobs.start_process(
                {
                    "operation": "composite",
                    "output_dir": str(run_dir),
                    "dataset": str(dataset_path),
                    "agent_config": str(self.agent_config_path),
                    "agent_spec": str(agent_path),
                    "evaluator_profile_path": (
                        str(profile_path) if profile_path is not None else None
                    ),
                    "attempts": attempts,
                    "run_workers": run_workers,
                    "score_workers": score_workers,
                    "mcp_config_path": (
                        str(self.mcp_config_path)
                        if self.mcp_config_path is not None
                        else None
                    ),
                    "trusted_dataset": bool(dataset.get("contains_live_answers")),
                    "pause_on_live_mismatch": True,
                },
                context={
                    "name": name.strip(),
                    "dataset_id": dataset_id,
                    "profile_id": profile_id,
                    "agent_spec": agent_spec,
                    "attempts": attempts,
                    "run_workers": run_workers,
                    "score_workers": score_workers,
                    "workspace_id": run_dir.name,
                },
            )
        except Exception:
            if metadata_path.is_file():
                metadata_path.unlink()
            run_dir.rmdir()
            raise

    def continue_evaluation(self, job_id: str) -> Dict[str, Any]:
        job = self.jobs.get(job_id)
        if job.get("status") != "attention_required":
            raise ValueError("evaluation is not awaiting attention")
        context = job["context"]
        workspace_id = context["workspace_id"]
        run_dir = self.catalog.runs_dir / workspace_id
        manifest = read_json(run_dir / "manifest.json")
        prepared_items = manifest["phases"]["prepare"]["prepared_items"]
        attention = manifest.get("attention_required") or {}
        if prepared_items <= attention.get("affected_item_count", 0):
            raise ValueError("no static or matching live question can continue")
        dataset_id = context["dataset_id"]
        profile_id = context["profile_id"]
        agent_filename = context["agent_spec"]
        return self.jobs.continue_process(
            job_id,
            {
                "operation": "continue",
                "output_dir": str(run_dir),
                "agent_config": str(self.agent_config_path),
                "agent_spec": str(self._agent_path(agent_filename)),
                "attempts": context["attempts"],
                "run_workers": context["run_workers"],
                "score_workers": context["score_workers"],
                "authorize_staged_invalid": True,
                "mcp_config_path": (
                    str(self.mcp_config_path)
                    if self.mcp_config_path is not None
                    else None
                ),
            },
        )

    def start_evaluation_retry(self, history_id: str) -> Dict[str, Any]:
        parent_path = self.history.run_path(history_id)
        parent = self.history.get_run_header(history_id)
        if not parent["capabilities"]["retry_failed"]:
            raise ValueError("legacy evaluation runs cannot be retried")
        workflow = QAWorkflow()
        plan = workflow.retry_plan(parent_path)
        parent_metadata = parent["metadata"]
        parent_name = parent_metadata.get("name") or parent["manifest"]["run_id"]
        retry_root_name = parent_metadata.get("retry_root_name") or parent_name
        previous_retry_number = parent_metadata.get("retry_number")
        retry_number = (
            previous_retry_number + 1
            if isinstance(previous_retry_number, int)
            and not isinstance(previous_retry_number, bool)
            and previous_retry_number > 0
            else 1
        )
        run_dir = self.catalog.runs_dir / str(uuid.uuid4())
        metadata = dict(parent_metadata)
        metadata.update(
            {
                "name": f"{retry_root_name} · retry {retry_number}",
                "created_at": utc_now(),
                "retry_of_history_id": history_id,
                "retry_number": retry_number,
                "retry_root_name": retry_root_name,
                "attempts": parent["manifest"]["attempts"],
            }
        )

        metadata_path = run_dir / "console_metadata.json"
        run_dir.mkdir()
        try:
            write_json(metadata_path, metadata)
            request = {
                "operation": "retry",
                "output_dir": str(run_dir),
                "parent_path": str(parent_path),
            }
            if self.mcp_config_path is not None:
                request["mcp_config_path"] = str(self.mcp_config_path)
            return self.jobs.start_process(
                request,
                context={
                    "name": metadata["name"],
                    "retry_of_history_id": history_id,
                    "retry_attempt_count": plan["retry_attempt_count"],
                    "attempts": parent["manifest"]["attempts"],
                    "workspace_id": run_dir.name,
                },
            )
        except Exception:
            if metadata_path.is_file():
                metadata_path.unlink()
            run_dir.rmdir()
            raise

    def cancel_evaluation(self, job_id: str) -> Dict[str, Any]:
        def persist_canceled(job: Dict[str, Any]) -> None:
            context = job["context"]
            workspace_id = context["workspace_id"]
            run_dir = self.catalog.runs_dir / workspace_id
            metadata = ConsoleMetadata.from_dict(
                read_json(run_dir / "console_metadata.json")
            )
            canceled = CanceledRunRecord(
                run_id=workspace_id,
                job_id=job_id,
                canceled_at=job["completed_at"],
                attempts=context["attempts"],
                metadata=metadata,
            )
            write_json(run_dir / "canceled.json", canceled.to_dict())

        job = self.jobs.cancel(job_id, on_terminated=persist_canceled)
        run_dir = self.catalog.runs_dir / job["context"]["workspace_id"]
        return {
            "job": job,
            "history_id": self.history.id_for_path(run_dir),
        }
