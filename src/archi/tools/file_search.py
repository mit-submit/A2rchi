"""Catalog-backed file search, metadata search, and document fetch tools.

Migrated from ``src.archi.pipelines.agents.tools.local_files`` to use
the Copilot SDK ``@define_tool`` decorator with Pydantic input models.

The ``RemoteCatalogClient`` is unchanged — it's imported from the
original module to avoid duplicating HTTP client code.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from langchain_core.documents import Document
from pydantic import BaseModel, Field

from src.archi.pipelines.agents.tools.local_files import (
    RemoteCatalogClient,
    _format_grep_hits,
    _format_files_for_llm,
    _render_metadata_preview,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


# ── Pydantic input models ────────────────────────────────────────────────

class FileSearchInput(BaseModel):
    query: str = Field(description="Search query string.")
    regex: bool = Field(default=False, description="Treat query as regex.")
    case_sensitive: bool = Field(default=False, description="Case-sensitive search.")
    max_results_override: Optional[int] = Field(default=None, description="Override default max results.")
    max_matches_per_file: int = Field(default=3, description="Max matches per file.")
    before: int = Field(default=0, description="Context lines before match.")
    after: int = Field(default=0, description="Context lines after match.")


class MetadataSearchInput(BaseModel):
    query: str = Field(description="Metadata query with optional key:value filters.")


class MetadataSchemaInput(BaseModel):
    pass  # No input required


class DocumentFetchInput(BaseModel):
    resource_hash: str = Field(description="Resource hash from a previous search hit.")
    max_chars: int = Field(default=4000, description="Max characters of document text.")


# ── Tool metadata for registry ───────────────────────────────────────────

FILE_SEARCH_NAME = "search_local_files"
FILE_SEARCH_DESCRIPTION = (
    "Grep-like search over local document contents only (not filenames/paths).\n"
    "Input: query (string), regex=false, case_sensitive=false, max_results_override=None, "
    "max_matches_per_file=3, before=0, after=0.\n"
    "Output: lines grouped by file with hash/path and matching line numbers, plus context lines.\n"
    'Example input: "timeout error" (regex=false).'
)

METADATA_SEARCH_NAME = "search_metadata_index"
METADATA_SEARCH_DESCRIPTION = (
    "Search document metadata stored in PostgreSQL (tickets, git, local files).\n"
    "Input: query string with key:value filters; filters are exact matches and ANDed "
    "within a group, OR across groups.\n"
    "Output: list of matches with hash, path, metadata, and a short snippet."
)

METADATA_SCHEMA_NAME = "list_metadata_schema"
METADATA_SCHEMA_DESCRIPTION = (
    "Return metadata schema hints: supported keys, distinct source_type values, and suffixes. "
    "Use this to learn which key:value filters are available before searching."
)

DOCUMENT_FETCH_NAME = "fetch_catalog_document"
DOCUMENT_FETCH_DESCRIPTION = (
    "Fetch a catalog document by resource hash after a search hit.\n"
    "Input: resource_hash (string), max_chars=4000.\n"
    "Output: path, metadata, and document text (truncated).\n"
    'Example input: "abcd1234".'
)


# ── Factory functions ────────────────────────────────────────────────────

def build_file_search_tool(
    catalog: RemoteCatalogClient,
    *,
    name: str = FILE_SEARCH_NAME,
    description: Optional[str] = None,
    max_results: int = 3,
    store_docs: Optional[Callable[[str, Sequence[Document]], None]] = None,
):
    from github_copilot_sdk import define_tool

    tool_description = description or FILE_SEARCH_DESCRIPTION

    @define_tool(name=name, description=tool_description, schema=FileSearchInput)
    async def _search_local_files(
        query: str,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results_override: Optional[int] = None,
        max_matches_per_file: int = 3,
        before: int = 0,
        after: int = 0,
    ) -> str:
        if not query.strip():
            return "Please provide a non-empty search query."

        limit = max_results_override or max_results
        try:
            results = catalog.search(
                query.strip(),
                limit=limit,
                search_content=True,
                regex=regex,
                case_sensitive=case_sensitive,
                max_matches_per_file=max_matches_per_file,
                before=before,
                after=after,
                mode="grep",
            )
        except Exception as exc:
            logger.warning("Catalog search failed: %s", exc)
            return "Catalog search failed."

        hits: List[Dict] = list(results)
        docs: List[Document] = []

        if store_docs and hits:
            for item in hits:
                try:
                    resource_hash = item.get("hash")
                    doc_payload = catalog.get_document(resource_hash, max_chars=4000) or {}
                    text = doc_payload.get("text") or ""
                    doc_meta = doc_payload.get("metadata") or item.get("metadata") or {}
                    docs.append(Document(page_content=text, metadata=doc_meta))
                except Exception:
                    continue

        if store_docs:
            store_docs(f"{name}: {query}", docs)

        return _format_grep_hits(hits)

    return _search_local_files


def build_metadata_search_tool(
    catalog: RemoteCatalogClient,
    *,
    name: str = METADATA_SEARCH_NAME,
    description: Optional[str] = None,
    max_results: int = 5,
    store_docs: Optional[Callable[[str, Sequence[Document]], None]] = None,
):
    from github_copilot_sdk import define_tool

    tool_description = description or METADATA_SEARCH_DESCRIPTION

    @define_tool(name=name, description=tool_description, schema=MetadataSearchInput)
    async def _search_metadata(query: str) -> str:
        if not query.strip():
            return "Please provide a non-empty search query."

        hits: List[Tuple[str, Path, Optional[Dict], str]] = []
        docs: List[Document] = []

        try:
            results = catalog.search(query.strip(), limit=max_results, search_content=False)
        except Exception as exc:
            logger.warning("Metadata search failed: %s", exc)
            return "Metadata search failed."

        for item in results:
            resource_hash = item.get("hash")
            path = Path(item.get("path", "")) if item.get("path") else Path("")
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            snippet = item.get("snippet") or ""
            hits.append((resource_hash, path, metadata, snippet))
            if len(hits) >= max_results:
                break

        if store_docs and hits:
            for resource_hash, path, metadata, _ in hits:
                try:
                    doc_payload = catalog.get_document(resource_hash, max_chars=4000) or {}
                    text = doc_payload.get("text") or ""
                    doc_meta = doc_payload.get("metadata") or metadata or {}
                    docs.append(Document(page_content=text, metadata=doc_meta))
                except Exception:
                    continue

        if store_docs:
            store_docs(f"{name}: {query}", docs)

        return _format_files_for_llm(hits)

    return _search_metadata


def build_metadata_schema_tool(
    catalog: RemoteCatalogClient,
    *,
    name: str = METADATA_SCHEMA_NAME,
    description: Optional[str] = None,
):
    from github_copilot_sdk import define_tool

    tool_description = description or METADATA_SCHEMA_DESCRIPTION

    @define_tool(name=name, description=tool_description, schema=MetadataSchemaInput)
    async def _schema_tool() -> str:
        try:
            payload = catalog.schema()
        except Exception as exc:
            logger.warning("Metadata schema fetch failed: %s", exc)
            return "Metadata schema fetch failed."
        keys = payload.get("keys") or []
        source_types = payload.get("source_types") or []
        suffixes = payload.get("suffixes") or []
        return (
            "Supported keys: " + ", ".join(keys) + "\n"
            "source_type values: " + (", ".join(source_types) or "none") + "\n"
            "suffix values: " + (", ".join(suffixes) or "none")
        )

    return _schema_tool


def build_document_fetch_tool(
    catalog: RemoteCatalogClient,
    *,
    name: str = DOCUMENT_FETCH_NAME,
    description: Optional[str] = None,
    default_max_chars: int = 4000,
):
    from github_copilot_sdk import define_tool

    tool_description = description or DOCUMENT_FETCH_DESCRIPTION

    @define_tool(name=name, description=tool_description, schema=DocumentFetchInput)
    async def _fetch_document(resource_hash: str, max_chars: int = default_max_chars) -> str:
        if not resource_hash.strip():
            return "Please provide a non-empty resource hash."

        try:
            doc_payload = catalog.get_document(resource_hash.strip(), max_chars=max_chars) or {}
        except Exception as exc:
            logger.warning("Document fetch failed: %s", exc)
            return "Document fetch failed."

        if not doc_payload:
            return "Document not found."

        path = doc_payload.get("path") or ""
        metadata = doc_payload.get("metadata") if isinstance(doc_payload.get("metadata"), dict) else {}
        text = doc_payload.get("text") or ""
        meta_preview = _render_metadata_preview(metadata)

        return (
            f"Path: {path}\n"
            f"Metadata:\n{meta_preview}\n\n"
            f"Content:\n{text}"
        )

    return _fetch_document
