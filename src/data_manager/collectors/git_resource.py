from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from src.data_manager.collectors.resource_base import BaseResource
from src.data_manager.collectors.utils.metadata import ResourceMetadata


@dataclass
class GitResource(BaseResource):
    """Representation of a single file harvested from a git repository."""

    repo_url: str           # canonical remote URL, credentials stripped
    file_path: str          # path within repo, e.g. "docs/guide.md"
    content: Union[str, bytes]
    source_type: str = "git"
    branch: str = ""
    ref: str = ""           # commit SHA or tag; used in blob URLs
    title: Optional[str] = None
    url: str = ""

    def get_hash(self) -> str:
        """
        Stable hash on (repo_url, file_path) so re-harvests overwrite in-place.

        Intentionally excludes ref/branch: the same file at a new commit
        is still the same resource — it should update the catalog entry,
        not create an orphan.
        """
        digest = hashlib.md5()
        digest.update(f"{self.repo_url}::{self.file_path}".encode("utf-8", errors="ignore"))
        return digest.hexdigest()[:12]

    def get_filename(self) -> str:
        return Path(self.file_path).name

    def get_file_path(self, target_dir: Path) -> Path:
        """Preserve the repo directory tree under target_dir."""
        return target_dir / self.file_path

    def get_content(self) -> Union[str, bytes]:
        return self.content

    def get_metadata(self) -> ResourceMetadata:
        extra: dict[str, str] = {
            "source_type": self.source_type,
            "repo_url": self.repo_url,
            "file_path": self.file_path,
            "suffix": Path(self.file_path).suffix.lstrip(".") or "",
            "display_name": self.file_path,
        }
        if self.url:
            extra["url"] = self.url
        if self.branch:
            extra["branch"] = self.branch
        if self.ref:
            extra["ref"] = self.ref
        if self.title:
            extra["title"] = self.title

        return ResourceMetadata(file_name=self.get_filename(), extra=extra)