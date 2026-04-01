"""Retriever tool — wraps a BaseRetriever for the Copilot SDK.

Factory: ``build_retriever_tool(retriever, *, store_docs, ...)``
Returns a ``@define_tool``-decorated async callable.

Core helpers (``_normalize_results``, ``_format_documents_for_llm``) are
imported from the LangGraph retriever module to avoid duplication — the
same pattern used by ``file_search`` and ``monit_search``.
"""

from __future__ import annotations

from typing import Callable, Optional, Sequence

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import BaseModel, Field

from src.archi.pipelines.agents.tools.retriever import (
    _format_documents_for_llm, _normalize_results)
from src.utils.logging import get_logger

logger = get_logger(__name__)


# ── Pydantic input model ─────────────────────────────────────────────────


class RetrieverInput(BaseModel):
    query: str = Field(description="Search query for the knowledge base.")


# ── Factory ──────────────────────────────────────────────────────────────

TOOL_NAME = "search_vectorstore_hybrid"
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
