from dataclasses import dataclass, field
from typing import Any, Dict, Union


@dataclass
class ScrapedResource:
    """Represents a single piece of scraped content."""

    url: str
    content: Union[str, bytes]
    suffix: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_binary(self) -> bool:
        """Return True if the content payload should be written as bytes."""
        return isinstance(self.content, (bytes, bytearray))