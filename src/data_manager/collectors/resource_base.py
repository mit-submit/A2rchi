from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union


class BaseResource(ABC):
    """Abstract representation of a persisted resource."""

    @abstractmethod
    def get_hash(self) -> str:
        """Return a unique identifier for the resource."""

    @abstractmethod
    def get_filename(self) -> str:
        """Return the filename (including extension) used when persisting the resource."""

    def get_file_path(self, target_dir: Path) -> Path:
        """Return the full path where the resource should be stored."""
        return target_dir / self.get_filename()

    def get_metadata_path(self, file_path: Path) -> Optional[Path]:
        """
        Return the path where metadata should be stored, or ``None`` if the resource
        does not produce auxiliary metadata.
        """
        metadata = self.get_metadata()
        if metadata is None:
            return None
        return file_path.with_suffix(f"{file_path.suffix}.meta.yaml")

    @abstractmethod
    def get_content(self) -> Union[str, bytes, bytearray]:
        """Return the resource content to be persisted."""
        ...

    def get_metadata(self):
        """Return a metadata object describing this resource, if available."""
        return None
