from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .artifacts import read_json, read_jsonl, verify_hashes
from .constants import SCHEMA_VERSION
from .preparation import load_preparation_records
from .tool_traces import serialize_tool_call_records


def _history_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:24]


class EvaluationHistory:
    """Read-only projection over persisted QA run artifacts."""

    def __init__(self, runs_dir: Path):
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _run_paths(self) -> List[Path]:
        root = self.runs_dir.resolve()
        return [
            path
            for path in self.runs_dir.iterdir()
            if path.is_dir() and not path.is_symlink() and path.resolve().parent == root
        ]

    def _resolve(self, history_id: str) -> Path:
        if not isinstance(history_id, str) or len(history_id) != 24:
            raise LookupError("evaluation run not found")
        for path in self._run_paths():
            if _history_id(path) == history_id:
                return path
        raise LookupError("evaluation run not found")

    def id_for_path(self, path: Path) -> str:
        resolved = Path(path).resolve()
        if resolved.parent != self.runs_dir.resolve() or not resolved.is_dir():
            raise ValueError("run path is outside the evaluation history")
        return _history_id(resolved)

    def run_path(self, history_id: str) -> Path:
        return self._resolve(history_id)

    @staticmethod
    def _load(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        manifest = read_json(path / "manifest.json")
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported run schema")
        run_id = manifest.get("run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("manifest run_id must be a non-empty string")
        status = manifest.get("status")
        if status not in {"prepared", "run_completed", "scored"}:
            raise ValueError("unsupported run status")
        phases = manifest.get("phases")
        if not isinstance(phases, dict):
            raise ValueError("manifest phases must be an object")
        required_phases = {
            "prepared": ("prepare",),
            "run_completed": ("prepare", "run"),
            "scored": ("prepare", "run", "score"),
        }[status]
        if any(
            not isinstance(phases.get(phase), dict)
            or phases[phase].get("status") != "completed"
            for phase in required_phases
        ):
            raise ValueError("manifest phase state is incomplete")
        if status != "prepared":
            attempts = manifest.get("attempts")
            if (
                isinstance(attempts, bool)
                or not isinstance(attempts, int)
                or attempts <= 0
            ):
                raise ValueError("manifest attempts must be a positive integer")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError("manifest artifacts must be an object")
        if any(
            not isinstance(name, str)
            or Path(name).name != name
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for name, digest in artifacts.items()
        ):
            raise ValueError("manifest contains an invalid artifact entry")
        input_details = manifest.get("input")
        if not isinstance(input_details, dict):
            raise ValueError("manifest input must be an object")
        snapshot = input_details.get("snapshot")
        if not isinstance(snapshot, str) or Path(snapshot).name != snapshot:
            raise ValueError("manifest input snapshot is invalid")
        if not {snapshot, "preparation.jsonl"}.issubset(artifacts):
            raise ValueError("manifest is missing preparation artifacts")
        if status == "scored" and not {
            "summary.json",
            "report.md",
        }.issubset(artifacts):
            raise ValueError("scored manifest is missing result artifacts")
        metadata_path = path / "console_metadata.json"
        metadata = {}
        if metadata_path.is_file() and "console_metadata.json" in manifest["artifacts"]:
            verify_hashes(path, manifest, ["console_metadata.json"])
            metadata = read_json(metadata_path)
        if not isinstance(metadata, dict):
            metadata = {}
        return manifest, metadata

    @staticmethod
    def _verify_present(
        path: Path, manifest: Dict[str, Any], filenames: List[str]
    ) -> None:
        present = [name for name in filenames if (path / name).is_file()]
        artifacts = manifest["artifacts"]
        undeclared = sorted(name for name in present if name not in artifacts)
        if undeclared:
            raise ValueError(
                "run contains undeclared artifact(s): " + ", ".join(undeclared)
            )
        declared_or_present = [
            name for name in filenames if name in artifacts or (path / name).is_file()
        ]
        verify_hashes(path, manifest, declared_or_present)

    def list_runs(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for path in self._run_paths():
            history_id = _history_id(path)
            try:
                manifest, metadata = self._load(path)
                summary = (
                    read_json(path / "summary.json")
                    if (path / "summary.json").is_file()
                    else None
                )
                self._verify_present(
                    path,
                    manifest,
                    ["summary.json"],
                )
                phases = manifest.get("phases") or {}
                timestamps = [
                    phase.get("completed_at") or phase.get("started_at")
                    for phase in phases.values()
                    if isinstance(phase, dict)
                ]
                created_at = metadata.get("created_at") or max(
                    (value for value in timestamps if value), default=""
                )
                rows.append(
                    {
                        "id": history_id,
                        "run_id": manifest.get("run_id"),
                        "name": metadata.get("name") or manifest.get("run_id"),
                        "status": manifest.get("status"),
                        "created_at": created_at,
                        "dataset_id": metadata.get("dataset_id"),
                        "dataset_name": metadata.get("dataset_name"),
                        "profile_id": metadata.get("profile_id"),
                        "profile_name": metadata.get("profile_name"),
                        "agent_spec": metadata.get("agent_spec"),
                        "attempts": manifest.get("attempts"),
                        "retry_of_history_id": metadata.get("retry_of_history_id"),
                        "retry_number": metadata.get("retry_number"),
                        "overall_attempt_pass_rate": (
                            summary.get("overall_attempt_pass_rate")
                            if isinstance(summary, dict)
                            else None
                        ),
                        "valid": True,
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "id": history_id,
                        "name": path.name,
                        "status": "invalid",
                        "created_at": "",
                        "valid": False,
                        "error": str(exc),
                    }
                )
        return sorted(rows, key=lambda row: row.get("created_at") or "", reverse=True)

    def get_run(self, history_id: str) -> Dict[str, Any]:
        path = self._resolve(history_id)
        manifest, metadata = self._load(path)
        snapshot = manifest["input"]["snapshot"]
        self._verify_present(
            path,
            manifest,
            [
                snapshot,
                "summary.json",
                "preparation.jsonl",
                "answers.jsonl",
                "evaluation_results.jsonl",
                "report.md",
            ],
        )
        payload: Dict[str, Any] = {
            "id": history_id,
            "manifest": manifest,
            "metadata": metadata,
        }
        preparation_path = path / "preparation.jsonl"
        preparation = load_preparation_records(
            preparation_path,
            expected_count=manifest["phases"]["prepare"]["input_items"],
        )
        payload["preparation"] = [record.to_dict() for record in preparation]
        payload["prepared_items"] = [
            record.to_dict()
            for record in preparation
            if record.status == "prepared"
        ]
        for filename, key in (
            ("summary.json", "summary"),
            ("answers.jsonl", "answers"),
            ("evaluation_results.jsonl", "evaluation_results"),
        ):
            artifact = path / filename
            if artifact.is_file():
                payload[key] = (
                    read_json(artifact)
                    if filename.endswith(".json")
                    else read_jsonl(artifact)
                )
        for index, answer in enumerate(payload.get("answers", []), 1):
            if not isinstance(answer, dict):
                raise ValueError(f"answer row {index} must be an object")
            if "tool_calls" in answer:
                answer["tool_calls"] = serialize_tool_call_records(
                    answer["tool_calls"],
                    context=f"answer row {index}.tool_calls",
                )
        report = path / "report.md"
        payload["report_available"] = report.is_file()
        return payload

    def get_report(self, history_id: str) -> str:
        path = self._resolve(history_id)
        manifest, _metadata = self._load(path)
        report = path / "report.md"
        if not report.is_file():
            raise LookupError("evaluation report not found")
        self._verify_present(path, manifest, ["report.md"])
        return report.read_text(encoding="utf-8")
