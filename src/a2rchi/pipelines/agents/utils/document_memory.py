"""Utilities for aggregating documents and notes during agent runs."""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from langchain_core.documents import Document


class DocumentMemory:
    """Track documents and textual annotations produced by agent tool calls."""
    # TODO for now we return langchain's Document objects. We could think about returning the same Resource classes we use when collecting these (or vice versa) to reduce the amount of dataclasses to worry about.
    # TODO we don't collect retriever scores

    def __init__(self) -> None:
        self._document_events: List[Tuple[str, List[Document]]] = []
        self._notes: List[str] = []

    def record(self, stage: str, documents: Iterable[Document]) -> None:
        """Store the documents captured for a specific stage or tool call."""
        docs_list: List[Document] = [doc for doc in documents if doc]
        if not docs_list:
            return
        self._document_events.append((stage, docs_list))
    
    def record_documents(self, stage: str, documents: Iterable[Document]) -> None:
        """Convenience wrapper that records documents and appends a note.

        This is a small helper used by agent callbacks so they can record a
        batch of documents and also create a short collector note in one call.
        """
        docs_list = [doc for doc in documents if doc]
        if not docs_list:
            return
        self.record(stage, docs_list)
        self.note(f"{stage} returned {len(docs_list)} document(s).")

    def note(self, message: str) -> None:
        """Append a textual note describing an intermediate step."""
        if not message:
            return
        self._notes.append(message)

    @property
    def notes(self) -> Sequence[str]:
        return tuple(self._notes)

    @property
    def events(self) -> Sequence[Tuple[str, List[Document]]]:
        return tuple(self._document_events)

    def unique_documents(self) -> List[Document]:
        """Return documents with simple deduplication by source metadata."""
        seen: set[Tuple[str, str, str]] = set()
        collected: List[Document] = []
        for _, docs in self._document_events:
            for doc in docs:
                key = self._document_key(doc)
                if key in seen:
                    continue
                seen.add(key)
                collected.append(doc)
        return collected

    def intermediate_steps(self) -> List[str]:
        """Combine stored notes with document-event breadcrumbs."""
        steps = list(self._notes)
        for stage, docs in self._document_events:
            steps.append(f"{stage}: {len(docs)} document(s)")
        return steps

    @staticmethod
    def _document_key(doc: Document) -> Tuple[str, str, str]:
        metadata = doc.metadata or {}
        return (
            str(metadata.get("document_id") or metadata.get("id") or metadata.get("source", "")),
            str(metadata.get("path") or metadata.get("file_path") or ""),
            doc.page_content[:200] if doc.page_content else "",
        )
