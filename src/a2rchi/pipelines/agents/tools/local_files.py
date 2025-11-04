from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from langchain.tools import tool
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from src.data_manager.collectors.utils.index_utils import CatalogService
from src.utils.logging import get_logger

logger = get_logger(__name__)


def _render_metadata_preview(metadata: Optional[Dict[str, object]], *, max_chars: int = 800) -> str:
    if not metadata:
        return "(no metadata)"
    # render key: value lines
    lines: List[str] = []
    for key, value in sorted(metadata.items()):
        lines.append(f"{key}: {value}")
    meta_str = "\n".join(lines)
    if len(meta_str) > max_chars:
        return meta_str[: max_chars - 12].rstrip() + "\n... (truncated)"
    return meta_str


def _format_files_for_llm(hits: List[Tuple[str, Path, Optional[Dict[str, object]], str]], *, max_meta_chars: int = 800, max_content_chars: int = 800) -> str:
    if not hits:
        return "No local files matched that search query."
    lines: List[str] = []
    for idx, (resource_hash, path, metadata, snippet) in enumerate(hits, start=1):
        meta_preview = _render_metadata_preview(metadata, max_chars=max_meta_chars)
        content = snippet.strip() if snippet else ""
        if len(content) > max_content_chars:
            content = content[: max_content_chars - 3].rstrip() + "..."
        lines.append(
            f"[{idx}] {path} (hash={resource_hash})\nMetadata:\n{meta_preview}\n\nSnippet:\n{content}"
        )
    return "\n\n".join(lines)


def _collect_snippet(text: str, match: re.Match, *, window: int = 240) -> str:
    start = max(match.start() - window, 0)
    end = min(match.end() + window, len(text))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    excerpt = text[start:end].replace("\n", " ")
    return f"{prefix}{excerpt}{suffix}"


def create_file_search_tool(
    catalog: CatalogService,
    *,
    name: str = "search_local_files",
    description: Optional[str] = None,
    max_results: int = 3,
    window: int = 240,
    store_docs: Optional[Callable[[str, Sequence[Path]], None]] = None,
) -> Callable[[str], str]:
    """Create a LangChain tool that performs keyword search in catalogued files."""

    _default_description = (
        "Search the locally stored source documents (text, markdown, csv, html, pdf)."
        "Provide a regex search query and the tool will return matching files, text snippets,"
        "and a unique hash for each file."
    )
    tool_description = (
        description
        or _default_description
    )

    @tool(name, description=tool_description)
    def _search_local_files(query: str) -> str:
        if not query.strip():
            return "Please provide a non-empty search query."

        pattern = re.compile(re.escape(query.strip()), re.IGNORECASE)
        hits: List[Tuple[str, Path, Optional[Dict[str, object]], str]] = []
        docs: List[Document] = []

        for resource_hash, path in catalog.iter_files():
            # attempt to load a Document via catalog helper
            doc = None
            try:
                doc = catalog.get_document_for_hash(resource_hash)
            except Exception:
                doc = None

            # search the content
            text = doc.page_content if doc else None
            if not text:
                continue
            match = pattern.search(text)
            if not match:
                continue

            # form the snippet to pass to the LLM
            snippet = _collect_snippet(text, match, window=window)
            metadata = None
            try:
                metadata = catalog.get_metadata_for_hash(resource_hash)
            except Exception:
                metadata = None
            hits.append((resource_hash, path, metadata, snippet))

            # store the Document
            if doc:
                docs.append(doc)

            if len(hits) >= max_results:
                break

        if store_docs:
            store_docs(f"{name}: {query}", docs)

        return _format_files_for_llm(hits)

    return _search_local_files


def _flatten_metadata(data: Dict[str, object], prefix: str = "") -> Dict[str, str]:
    flattened: Dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten_metadata(value, prefix=full_key))
        else:
            flattened[full_key] = "" if value is None else str(value)
    return flattened


def create_metadata_search_tool(
    catalog: CatalogService,
    *,
    name: str = "search_metadata_index",
    description: Optional[str] = None,
    max_results: int = 5,
    store_docs: Optional[Callable[[str, Sequence[Path]], None]] = None,
) -> Callable[[str], str]:
    """Create a LangChain tool to search resource metadata catalogues."""

    tool_description = (
        description
        or "Search the metadata entries associated with the local document catalog."
    )

    @tool(name, description=tool_description)
    def _search_metadata(query: str) -> str:
        if not query.strip():
            return "Please provide a non-empty search query."

        hits: List[Tuple[str, Path, Optional[Dict[str, object]], str]] = []
        docs: List[Document] = []
        query_lower = query.lower()

        for resource_hash, _ in catalog.metadata_index.items():
            resource_metadata = catalog.get_metadata_for_hash(resource_hash)
            if not isinstance(resource_metadata, dict):
                continue

            flattened = _flatten_metadata(resource_metadata)
            matches = {
                key: value
                for key, value in flattened.items()
                if query_lower in key.lower() or query_lower in value.lower()
            }
            if not matches:
                continue

            # obtain file path and content for snippet
            try:
                doc = catalog.get_document_for_hash(resource_hash)
            except Exception:
                doc = None
            text = doc.page_content if doc else None

            # build snippet from content
            path = catalog.get_filepath_for_hash(resource_hash)
            hits.append((resource_hash, path, resource_metadata, text))

            # store docs
            if doc:
                docs.append(doc)

            if len(hits) >= max_results:
                break

        if store_docs:
            store_docs(f"{name}: {query}", docs)

        return _format_files_for_llm(hits)

    return _search_metadata


__all__ = [
    "create_retriever_tool",
    "create_file_search_tool",
    "create_metadata_search_tool",
]