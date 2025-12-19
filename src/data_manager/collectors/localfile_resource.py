from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.data_manager.collectors.resource_base import BaseResource
from src.data_manager.collectors.utils.metadata import ResourceMetadata


@dataclass
class LocalFileResource(BaseResource):
    """Representation of a file copied from the host filesystem."""

    file_name: str
    source_path: Path
    content: bytes
    source_type: str = "local_files"
    base_dir: Optional[Path] = None

    def get_hash(self) -> str:
        """Stable hash based on the path so updates overwrite in-place."""
        digest = hashlib.md5()
        digest.update(self._hash_key().encode("utf-8", errors="ignore"))
        return digest.hexdigest()[:12]

    def get_filename(self) -> str:
        suffix = self.source_path.suffix
        return f"{self.get_hash()}{suffix}" if suffix else self.get_hash()

    def get_content(self) -> bytes:
        return self.content

    def get_metadata(self) -> ResourceMetadata:
        stats = self.source_path.stat()
        relative_path = self._relative_path()
        display_name = relative_path or self.source_path.name

        extra = {
            "source_type": self.source_type,
            "original_path": str(self.source_path.resolve()),
            "suffix": self.source_path.suffix or "",
            "size_bytes": str(stats.st_size),
            "modified_at": datetime.fromtimestamp(stats.st_mtime, tz=timezone.utc).isoformat(),
        }

        if relative_path:
            extra["relative_path"] = relative_path
        if self.base_dir:
            extra["base_path"] = str(self.base_dir.resolve())

        return ResourceMetadata(display_name=display_name, extra=extra)

    def _relative_path(self) -> Optional[str]:
        if not self.base_dir:
            return None
        try:
            return str(self.source_path.relative_to(self.base_dir))
        except Exception:
            return None

    def _hash_key(self) -> str:
        relative = self._relative_path()
        return relative or str(self.source_path.resolve())
