from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class TicketResource:
    """Standard representation of a collected support ticket."""

    ticket_id: str
    content: str
    source: str
    created_at: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_index_record(self) -> Dict[str, Any]:
        """Return a serialisable summary for persistence in an index file."""
        record: Dict[str, Any] = {
            "ticket_id": self.ticket_id,
            "source": self.source,
        }
        if self.created_at:
            record["created_at"] = self.created_at
        if self.metadata:
            record["metadata"] = dict(self.metadata)
        return record