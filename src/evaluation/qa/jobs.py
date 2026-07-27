from __future__ import annotations

import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .artifacts import read_json, utc_now, write_json


class JobConflictError(RuntimeError):
    pass


class EvaluationJobManager:
    """Persisted, single-flight execution for provider-backed QA work."""

    def __init__(self, jobs_dir: Path):
        self.jobs_dir = Path(jobs_dir)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="archi-qa"
        )
        self._futures: Dict[str, Future] = {}
        self._interrupt_stale_jobs()

    def _path(self, job_id: str) -> Path:
        try:
            if str(uuid.UUID(job_id)) != job_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise LookupError("evaluation job not found") from exc
        return self.jobs_dir / f"{job_id}.json"

    def _interrupt_stale_jobs(self) -> None:
        for path in self.jobs_dir.glob("*.json"):
            try:
                job = read_json(path)
            except ValueError:
                continue
            if job.get("status") in {"queued", "running"}:
                job["status"] = "interrupted"
                job["completed_at"] = utc_now()
                job["error"] = "service restarted before the job completed"
                write_json(path, job)

    def _active(self) -> Optional[Dict[str, Any]]:
        for path in self.jobs_dir.glob("*.json"):
            try:
                job = read_json(path)
            except ValueError:
                continue
            if job.get("status") in {"queued", "running"}:
                return job
        return None

    def start(
        self,
        kind: str,
        work: Callable[[], Optional[Dict[str, Any]]],
        *,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if kind not in {"generate_atoms", "evaluation"}:
            raise ValueError("unsupported evaluation job kind")
        with self._lock:
            active = self._active()
            if active is not None:
                raise JobConflictError(
                    f"job {active['id']} is already {active['status']}"
                )
            job_id = str(uuid.uuid4())
            job = {
                "id": job_id,
                "kind": kind,
                "status": "queued",
                "created_at": utc_now(),
                "started_at": None,
                "completed_at": None,
                "context": context or {},
            }
            write_json(self._path(job_id), job)
            try:
                self._futures[job_id] = self._executor.submit(
                    self._execute, job_id, work
                )
            except Exception:
                job["status"] = "failed"
                job["completed_at"] = utc_now()
                job["error"] = "could not submit job"
                write_json(self._path(job_id), job)
                raise
            return job

    def _execute(
        self, job_id: str, work: Callable[[], Optional[Dict[str, Any]]]
    ) -> None:
        with self._lock:
            job = read_json(self._path(job_id))
            job["status"] = "running"
            job["started_at"] = utc_now()
            write_json(self._path(job_id), job)
        try:
            result = work() or {}
            if not isinstance(result, dict):
                raise ValueError("job result must be an object")
            with self._lock:
                job = read_json(self._path(job_id))
                job["status"] = "completed"
                job["completed_at"] = utc_now()
                job["result"] = result
                write_json(self._path(job_id), job)
        except Exception as exc:
            with self._lock:
                job = read_json(self._path(job_id))
                job["status"] = "failed"
                job["completed_at"] = utc_now()
                job["error"] = str(exc)
                write_json(self._path(job_id), job)

    def get(self, job_id: str) -> Dict[str, Any]:
        path = self._path(job_id)
        if not path.is_file():
            raise LookupError("evaluation job not found")
        return read_json(path)

    def list(self) -> List[Dict[str, Any]]:
        jobs = []
        for path in self.jobs_dir.glob("*.json"):
            try:
                jobs.append(read_json(path))
            except ValueError:
                continue
        return sorted(jobs, key=lambda job: job.get("created_at", ""), reverse=True)

    def wait(self, job_id: str, timeout: Optional[float] = None) -> Dict[str, Any]:
        future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.get(job_id)

    def close(self) -> None:
        self._executor.shutdown(wait=False)
