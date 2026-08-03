from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.archi.pipelines.agents.agent_spec import list_agent_files

from .artifacts import read_json, sha256_file, utc_now, write_json
from .catalog import EvaluationCatalog
from .history import EvaluationHistory
from .jobs import EvaluationJobManager
from .profile import load_profile
from .runtime import LangChainEvaluatorRuntime
from .workflow import QAWorkflow


class EvaluationConsoleService:
    """Application service joining catalogs, jobs, history, and QAWorkflow."""

    def __init__(
        self,
        root: Path,
        *,
        agent_config_path: Path,
        agents_dir: Path,
        workflow_factory: Callable[[], QAWorkflow] = QAWorkflow,
    ):
        self.catalog = EvaluationCatalog(root)
        self.history = EvaluationHistory(self.catalog.runs_dir)
        self.jobs = EvaluationJobManager(self.catalog.jobs_dir)
        self.agent_config_path = Path(agent_config_path)
        self.agents_dir = Path(agents_dir)
        self.workflow_factory = workflow_factory

    def list_agents(self) -> List[Dict[str, str]]:
        return [
            {"id": path.name, "name": path.stem}
            for path in list_agent_files(self.agents_dir)
        ]

    def get_job(self, job_id: str) -> Dict[str, Any]:
        job = self.jobs.get(job_id)
        result = job.get("result") or {}
        draft_id = result.get("draft_id")
        if job.get("kind") == "generate_atoms" and draft_id:
            try:
                draft = self.catalog.get_atom_draft(draft_id)
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
        return job

    def list_jobs(self) -> List[Dict[str, Any]]:
        return [self.get_job(job["id"]) for job in self.jobs.list()]

    def _agent_path(self, agent_filename: str) -> Path:
        if (
            not isinstance(agent_filename, str)
            or Path(agent_filename).name != agent_filename
            or Path(agent_filename).suffix.lower() != ".md"
        ):
            raise ValueError("agent_spec must be a catalog agent filename")
        available = {path.name: path for path in list_agent_files(self.agents_dir)}
        try:
            return available[agent_filename]
        except KeyError as exc:
            raise ValueError("selected agent spec does not exist") from exc

    def start_atom_generation(self, dataset_id: str, profile_id: str) -> Dict[str, Any]:
        dataset = self.catalog.get_dataset(dataset_id)
        if dataset["atom_count"] != 0:
            raise ValueError(
                "atom generation requires a dataset with zero atoms; review its "
                "existing atoms instead"
            )
        profile_path = self.catalog.profile_path(profile_id)

        def work() -> Dict[str, Any]:
            evaluator = LangChainEvaluatorRuntime(load_profile(profile_path))
            draft = self.catalog.create_atom_draft(dataset_id, profile_id, evaluator)
            return {"draft_id": draft["id"]}

        return self.jobs.start(
            "generate_atoms",
            work,
            context={"dataset_id": dataset_id, "profile_id": profile_id},
        )

    def start_atom_retry(self, draft_id: str) -> Dict[str, Any]:
        details = self.catalog.atom_retry_details(draft_id)
        profile_path = self.catalog.profile_path(details["profile_id"])

        def work() -> Dict[str, Any]:
            evaluator = LangChainEvaluatorRuntime(load_profile(profile_path))
            draft = self.catalog.retry_failed_atom_items(draft_id, evaluator)
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
        profile_path = self.catalog.profile_path(profile_id)
        agent_path = self._agent_path(agent_spec)
        if not self.agent_config_path.is_file():
            raise ValueError("configured evaluation agent config does not exist")

        run_dir = self.catalog.runs_dir / str(uuid.uuid4())
        metadata = {
            "name": name.strip(),
            "dataset_id": dataset_id,
            "dataset_name": dataset["name"],
            "profile_id": profile_id,
            "profile_name": profile["name"],
            "agent_spec": agent_spec,
            "run_workers": run_workers,
            "score_workers": score_workers,
            "created_at": utc_now(),
        }

        def work() -> Dict[str, Any]:
            run_dir.mkdir()
            write_json(run_dir / "console_metadata.json", metadata)
            manifest = self.workflow_factory().composite(
                dataset=dataset_path,
                agent_config=self.agent_config_path,
                agent_spec=agent_path,
                output_dir=run_dir,
                evaluator_profile_path=profile_path,
                attempts=attempts,
                run_workers=run_workers,
                score_workers=score_workers,
                overwrite=False,
            )
            manifest["artifacts"]["console_metadata.json"] = sha256_file(
                run_dir / "console_metadata.json"
            )
            write_json(run_dir / "manifest.json", manifest)
            return {
                "run_id": manifest["run_id"],
                "history_id": self.history.id_for_path(run_dir),
            }

        return self.jobs.start(
            "evaluation",
            work,
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

    def start_evaluation_retry(self, history_id: str) -> Dict[str, Any]:
        parent_path = self.history.run_path(history_id)
        parent = self.history.get_run(history_id)
        workflow = self.workflow_factory()
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
            }
        )

        def work() -> Dict[str, Any]:
            run_dir.mkdir()
            write_json(run_dir / "console_metadata.json", metadata)
            manifest = self.workflow_factory().retry(parent_path, run_dir)
            manifest["artifacts"]["console_metadata.json"] = sha256_file(
                run_dir / "console_metadata.json"
            )
            write_json(run_dir / "manifest.json", manifest)
            return {
                "run_id": manifest["run_id"],
                "history_id": self.history.id_for_path(run_dir),
                "retry_of_history_id": history_id,
            }

        return self.jobs.start(
            "evaluation",
            work,
            context={
                "name": metadata["name"],
                "retry_of_history_id": history_id,
                "retry_attempt_ids": plan["retry_attempt_ids"],
                "workspace_id": run_dir.name,
            },
        )
