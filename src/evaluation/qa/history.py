from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set, Tuple

from .artifacts import iter_jsonl, read_json, read_jsonl, verify_hashes
from .constants import SCHEMA_VERSION
from .preparation import load_preparation_records, load_preparation_rows
from .tool_traces import serialize_tool_call_records

LEGACY_SCHEMA_VERSION = "qa-v0"
_PREPARATION_FILES = {
    LEGACY_SCHEMA_VERSION: ("prepared_items.jsonl", "preparation_results.jsonl"),
    SCHEMA_VERSION: ("preparation.jsonl",),
}


def _history_id(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:24]


def _require_exact_fields(
    row: Dict[str, Any], expected: Set[str], *, context: str
) -> None:
    missing = sorted(expected - set(row))
    unknown = sorted(set(row) - expected)
    details = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if unknown:
        details.append("unknown: " + ", ".join(unknown))
    if details:
        raise ValueError(f"{context} has invalid fields ({'; '.join(details)})")


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
    def _preparation_files(schema_version: str) -> Tuple[str, ...]:
        try:
            return _PREPARATION_FILES[schema_version]
        except KeyError as exc:
            raise ValueError("unsupported run schema") from exc

    @staticmethod
    def _capabilities(schema_version: str) -> Dict[str, bool]:
        return {"retry_failed": schema_version == SCHEMA_VERSION}

    @staticmethod
    def _load(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        manifest = read_json(path / "manifest.json")
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        schema_version = manifest.get("schema_version")
        if not isinstance(schema_version, str):
            raise ValueError("unsupported run schema")
        preparation_files = EvaluationHistory._preparation_files(schema_version)
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
        if not {snapshot, *preparation_files}.issubset(artifacts):
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
    def _legacy_preparation_rows(path: Path) -> Iterator[Dict[str, Any]]:
        metadata_fields = {
            "item_id",
            "category",
            "answer_mode",
            "answer_source",
        }
        prepared_fields = metadata_fields | {
            "question",
            "answer",
            "time_sensitive",
            "atom_source",
            "gold_atoms",
        }
        prepared_by_id: Dict[str, Dict[str, Any]] = {}
        for index, row in enumerate(iter_jsonl(path / "prepared_items.jsonl"), 1):
            _require_exact_fields(
                row, prepared_fields, context=f"legacy prepared row {index}"
            )
            item_id = row["item_id"]
            if not isinstance(item_id, str) or not item_id.strip():
                raise ValueError(f"legacy prepared row {index} has an invalid item ID")
            if item_id in prepared_by_id:
                raise ValueError(
                    "legacy preparation contains duplicate prepared item IDs"
                )
            prepared_by_id[item_id] = row

        seen_results = set()
        for index, result in enumerate(
            iter_jsonl(path / "preparation_results.jsonl"), 1
        ):
            status = result.get("status")
            status_fields = {
                "prepared": set(),
                "preparation_failed": {"error"},
                "skipped_time_sensitive": set(),
            }
            if not isinstance(status, str) or status not in status_fields:
                raise ValueError(
                    f"legacy preparation result row {index} has an unsupported status"
                )
            _require_exact_fields(
                result,
                metadata_fields | {"status"} | status_fields[status],
                context=f"legacy preparation result row {index}",
            )
            item_id = result["item_id"]
            if not isinstance(item_id, str) or not item_id.strip():
                raise ValueError(
                    f"legacy preparation result row {index} has an invalid item ID"
                )
            if item_id in seen_results:
                raise ValueError(
                    "legacy preparation contains duplicate result item IDs"
                )
            seen_results.add(item_id)
            if status != "prepared":
                yield result
                continue

            try:
                prepared = prepared_by_id.pop(item_id)
            except KeyError as exc:
                raise ValueError(
                    "legacy prepared result has no matching prepared item"
                ) from exc
            if any(
                prepared[field] != result[field]
                for field in metadata_fields - {"item_id"}
            ):
                raise ValueError("legacy preparation metadata is inconsistent")
            yield {**prepared, "status": "prepared"}

        if prepared_by_id:
            raise ValueError("legacy prepared item has no matching preparation result")

    @staticmethod
    def _load_preparation(
        path: Path, manifest: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        expected_count = manifest["phases"]["prepare"]["input_items"]
        if manifest["schema_version"] == LEGACY_SCHEMA_VERSION:
            records = load_preparation_rows(
                EvaluationHistory._legacy_preparation_rows(path),
                expected_count=expected_count,
            )
        else:
            records = load_preparation_records(
                path / "preparation.jsonl", expected_count=expected_count
            )
        return [record.to_dict() for record in records]

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
                        "schema_version": manifest["schema_version"],
                        "capabilities": self._capabilities(
                            manifest["schema_version"]
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
        preparation_files = self._preparation_files(manifest["schema_version"])
        self._verify_present(
            path,
            manifest,
            [
                snapshot,
                "summary.json",
                *preparation_files,
                "answers.jsonl",
                "evaluation_results.jsonl",
                "report.md",
            ],
        )
        payload: Dict[str, Any] = {
            "id": history_id,
            "manifest": manifest,
            "metadata": metadata,
            "capabilities": self._capabilities(manifest["schema_version"]),
        }
        preparation = self._load_preparation(path, manifest)
        payload["preparation"] = preparation
        payload["prepared_items"] = [
            record
            for record in preparation
            if record["status"] == "prepared"
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
