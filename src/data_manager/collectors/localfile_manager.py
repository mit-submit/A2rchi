from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from src.data_manager.collectors.localfile_resource import LocalFileResource
from src.data_manager.collectors.persistence import PersistenceService
from src.utils.config_access import get_global_config
from src.utils.logging import get_logger

logger = get_logger(__name__)


class LocalFileManager:
    """Collects local files/directories into the shared data path."""

    def __init__(self, dm_config: Optional[Dict[str, Any]] = None) -> None:
        global_config = get_global_config()
        self.data_path = Path(global_config["DATA_PATH"])

        sources_config = (dm_config or {}).get("sources", {}) or {}
        self.config = dict(sources_config.get("local_files", {})) if isinstance(sources_config, dict) else {}

        self.enabled = self.config.get("enabled", True)
        base_dir = self.config.get("base_dir")
        self.base_dir: Optional[Path] = Path(base_dir).expanduser() if base_dir else None
        self.overwrite = bool(self.config.get("overwrite", True))
        self.staging_dir = Path(self.config.get("staging_dir") or (self.data_path / "raw_local_files"))

    def collect_all_from_config(self, persistence: PersistenceService) -> None:
        if not self.enabled:
            logger.info("Local files disabled; skipping")
            return
        source_root = self.staging_dir
        if not source_root.exists():
            logger.info("Local files directory does not exist: %s", source_root)
            return

        target_dir = self.data_path / "local_files"
        for file_path in self._iter_files(source_root):
            self._persist_file(file_path, persistence, target_dir, base_dir=self.base_dir or source_root)

    def schedule_collect_local_files(self, persistence: PersistenceService, last_run: Optional[str] = None) -> None:
        """For now simply re-run the configured collection."""
        self.collect_all_from_config(persistence)

    def ingest_uploaded_file(self, upload: FileStorage, persistence: PersistenceService) -> Path:
        """Persist a single uploaded file into the local_files source."""
        if not self.enabled:
            raise ValueError("Local files source is disabled")

        filename = secure_filename(upload.filename or "")
        if not filename:
            raise ValueError("No filename provided")

        self.staging_dir.mkdir(parents=True, exist_ok=True)
        staging_path = self.staging_dir / filename
        upload.save(staging_path)

        target_dir = self.data_path / "local_files"
        return self._persist_file(staging_path, persistence, target_dir, base_dir=self.base_dir or self.staging_dir)

    def ingest_directory(
        self,
        directory: Path,
        persistence: PersistenceService,
        *,
        source_type: str = "local_files",
        extra_metadata: Optional[Dict[str, Any]] = None,
        target_subdir: Optional[str] = None,
    ) -> list[Dict[str, Any]]:
        """Recursively persist every file under ``directory`` into the catalog.

        Used by the cross-container ingest endpoint to pull in files dropped by
        another service (e.g. the Indico MCP container's authenticated downloads)
        via a shared volume. Returns a list of ``{"hash", "relative_path",
        "filename"}`` records for the agent to feed back into the catalog.
        """
        directory = Path(directory)
        if not directory.exists() or not directory.is_dir():
            raise ValueError(f"Not a directory: {directory}")

        target_dir = self.data_path / (target_subdir or source_type)
        results: list[Dict[str, Any]] = []
        total = 0
        for file_path in self._iter_files(directory):
            total += 1
            record = self._persist_file(
                file_path,
                persistence,
                target_dir,
                base_dir=directory,
                source_type=source_type,
                extra_metadata=extra_metadata or {},
                return_record=True,
            )
            if record:
                results.append(record)
        logger.info(
            "ingest_directory: persisted %d/%d file(s) from %s (source_type=%s%s)",
            len(results),
            total,
            directory,
            source_type,
            f", event_id={(extra_metadata or {}).get('event_id')}" if (extra_metadata or {}).get("event_id") else "",
        )
        return results

    # internal helpers

    def _iter_files(self, directory: Path) -> Iterable[Path]:
        for file_path in directory.rglob("*"):
            if file_path.is_file():
                yield file_path

    def _persist_file(
        self,
        path: Path,
        persistence: PersistenceService,
        target_dir: Path,
        *,
        base_dir: Optional[Path],
        source_type: str = "local_files",
        extra_metadata: Optional[Dict[str, Any]] = None,
        return_record: bool = False,
    ):
        """Persist a single file via the catalog. Returns:
          - the persisted ``Path`` on success (default), or
          - a ``{"hash","filename","relative_path"}`` dict when ``return_record=True``,
          - ``None`` on read/persist failure.
        Read and persist errors are logged but swallowed so directory walks keep going.
        """
        try:
            content = path.read_bytes()
        except Exception as exc:
            logger.warning("Failed to read local file %s: %s", path, exc)
            return None

        resource = LocalFileResource(
            file_name=path.name,
            source_path=path,
            content=content,
            source_type=source_type,
            base_dir=base_dir,
            extra_metadata=dict(extra_metadata or {}),
        )
        effective_target_dir = self._resolve_target_dir(path, target_dir, base_dir)
        try:
            persisted_path = persistence.persist_resource(resource, effective_target_dir, overwrite=self.overwrite)
        except Exception as exc:
            logger.warning("Failed to persist local file %s: %s", path, exc)
            return None

        if not return_record:
            return persisted_path

        try:
            relative_path = str(persisted_path.relative_to(self.data_path))
        except ValueError:
            relative_path = str(persisted_path)
        return {
            "hash": resource.get_hash(),
            "filename": resource.get_filename(),
            "relative_path": relative_path,
        }

    def _resolve_target_dir(self, path: Path, target_dir: Path, base_dir: Optional[Path]) -> Path:
        if not base_dir:
            return target_dir
        try:
            relative_path = path.relative_to(base_dir)
        except ValueError:
            return target_dir
        if relative_path.parent == Path("."):
            return target_dir
        return target_dir / relative_path.parent
