from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class ResourceMetadata:
    """Lightweight container for resource metadata."""

    display_name: str
    extra: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # dataclasses with ``frozen=True`` use ``object.__setattr__`` for validation
        if not isinstance(self.display_name, str) or not self.display_name:
            raise ValueError("display_name must be a non-empty string")

        sanitized: Dict[str, str] = {}
        for key, value in self.extra.items():
            if not isinstance(key, str):
                raise TypeError("metadata keys must be strings")
            if not isinstance(value, str):
                raise TypeError("metadata values must be strings")
            sanitized[key] = value

        object.__setattr__(self, "extra", sanitized)

    def as_dict(self) -> Dict[str, str]:
        """Return a flat dictionary representation."""
        return {"display_name": self.display_name, **self.extra}
