from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

INDEXED_METADATA_KEYS: Tuple[str, ...] = (
    "file_name",
    "source_type",
    "url",
    "ticket_id",
    "suffix",
    "size_bytes",
    "original_path",
    "base_path",
    "relative_path",
    "created_at",
    "modified_at",
    "ingested_at",
    "chunk_source",
)


@dataclass(frozen=True)
class ResourceMetadata:
    """Lightweight container for resource metadata."""

    file_name: str
    extra: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # dataclasses with ``frozen=True`` use ``object.__setattr__`` for validation
        if not isinstance(self.file_name, str) or not self.file_name:
            raise ValueError("file_name must be a non-empty string")

        sanitized: Dict[str, str] = {}
        for key, value in self.extra.items():
            if not isinstance(key, str):
                raise TypeError("metadata keys must be strings")
            if not isinstance(value, str):
                raise TypeError("metadata values must be strings")
            if key == "file_name":
                raise ValueError("file_name must be provided as a top-level field")
            sanitized[key] = value

        object.__setattr__(self, "extra", sanitized)

    def as_dict(self) -> Dict[str, str]:
        """Return a flat dictionary representation."""
        return {"file_name": self.file_name, **self.extra}
