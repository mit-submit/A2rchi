"""Per-request document collector for source attribution.

Provides a ``store_docs`` callback that tools call to record retrieved
documents, plus an ``on_post_tool_use`` hook handler for MCP / built-in
tools whose output might contain document references.

After a request completes, the pipeline reads ``unique_documents()`` and
``scores()`` to populate the final PipelineOutput (decision 5, 11).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from langchain_core.documents import Document

from src.utils.logging import get_logger

logger = get_logger(__name__)


class DocumentCollector:
    """Accumulates documents retrieved by tools during a single request."""

    def __init__(self) -> None:
        self._docs: List[Tuple[str, Document]] = []  # (tool_label, doc)
        self._scores: List[float] = []

    def store_docs(self, tool_label: str, docs: Sequence[Document]) -> None:
        """Callback passed to tool factories as ``store_docs``."""
        for doc in docs:
            self._docs.append((tool_label, doc))

    def store_docs_with_scores(
        self,
        tool_label: str,
        docs: Sequence[Document],
        scores: Optional[Sequence[float]] = None,
    ) -> None:
        for i, doc in enumerate(docs):
            self._docs.append((tool_label, doc))
            if scores and i < len(scores):
                self._scores.append(scores[i])

    def unique_documents(self) -> List[Document]:
        """De-duplicate by page_content hash, preserving insertion order."""
        seen: set = set()
        result: List[Document] = []
        for _, doc in self._docs:
            key = hash(doc.page_content)
            if key not in seen:
                seen.add(key)
                result.append(doc)
        return result

    def scores(self) -> List[float]:
        return list(self._scores)

    def make_store_docs_callback(self) -> Callable[[str, Sequence[Document]], None]:
        """Return a bound callback suitable for tool factory ``store_docs`` param."""
        return self.store_docs
