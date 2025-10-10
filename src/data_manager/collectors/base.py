from __future__ import annotations

from typing import Protocol


class Collector(Protocol):
    """Protocol for classes capable of collecting data into persistence."""

    def collect(self, persistence: "PersistenceService") -> None:
        """Execute the collection and persist results using the provided service."""
        ...