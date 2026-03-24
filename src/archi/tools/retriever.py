"""Retriever tool — wraps a BaseRetriever for the Copilot SDK.

Factory: ``build_retriever_tool(retriever, *, store_docs, ...)``
Returns a ``@define_tool``-decorated async callable.
"""

from __future__ import annotations

from typing import Callable, Iterable, Optional, Sequence, Tuple

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import BaseModel, Field

from src.utils.logging import get_logger

logger = get_logger(__name__)


# ── Helpers (shared with old code, no changes) ────────────────────────────

def _normalize_results(
    results: Iterable[object],
) -> Sequence[Tuple[Document, Optional[float]]]:
    normalized: list[Tuple[Document, Optional[float]]] = []
    for item in results:
        if isinstance(item, Document):
            normalized.append((item, None))
        elif (
            isinstance(item, tuple)
            and len(item) >= 2
            and isinstance(item[0], Document)
        ):
            normalized.append((item[0], item[1]))
    return normalized


def _format_documents_for_llm(
    docs: Sequence[Tuple[Document, Optional[float]]],
    *,
    max_documents: int,
    max_chars: int,
) -> str:
    if not docs:
        return "No documents found in the knowledge base for this query."

    snippets = []
    for idx, (doc, score) in enumerate(docs[:max_documents], start=1):
        source = doc.metadata.get("filename") or "unknown source"
        hash_val = doc.metadata.get("resource_hash") or "n/a"
        text = doc.page_content.strip()
        if len(text) > max_chars:
            text = f"{text[:max_chars].rstrip()}..."
        header = f"[{idx}] {source} (hash={hash_val})"
        footer = f"Score: {score:.4f}" if isinstance(score, (float, int)) else "Score: n/a"
        snippets.append(f"{header}\n{footer}\n{text}")

    return "\n\n".join(snippets)


# ── Pydantic input model ─────────────────────────────────────────────────

class RetrieverInput(BaseModel):
    query: str = Field(description="Search query for the knowledge base.")


# ── Factory ──────────────────────────────────────────────────────────────

TOOL_NAME = "search_knowledge_base"
TOOL_DESCRIPTION = (
    "Search the indexed knowledge base for relevant passages.\n"
    "Input: query string.\n"
    "Output: ranked snippets with source filename, resource hash, and score.\n"
    'Example input: "transfer errors in CMS".'
)


def build_retriever_tool(
    retriever: BaseRetriever,
    *,
    name: str = TOOL_NAME,
    description: Optional[str] = None,
    max_documents: int = 4,
    max_chars: int = 800,
    store_docs: Optional[Callable[[str, Sequence[Document]], None]] = None,
):
    """Return a ``@define_tool``-decorated async function.

    Dependencies are captured via closure — the returned callable only
    receives the Pydantic-validated ``RetrieverInput`` at invocation time.
    """
    from copilot import define_tool  # deferred import

    tool_description = description or TOOL_DESCRIPTION

    @define_tool(name=name, description=tool_description)
    async def _retriever_tool(params: RetrieverInput) -> str:
        query = params.query
        results = retriever.invoke(query)
        docs = _normalize_results(results or [])
        if store_docs:
            store_docs(f"{name}: {query}", [doc for doc, _ in docs])
        return _format_documents_for_llm(
            docs, max_documents=max_documents, max_chars=max_chars
        )

    return _retriever_tool
