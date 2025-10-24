from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from langchain.tools import tool
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from src.data_manager.collectors.utils.index_utils import (
    load_index,
    load_sources_catalog,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Retriever tool wrapper
# ---------------------------------------------------------------------------


def _normalize_results(
    results: Iterable[object],
) -> Sequence[Tuple[Document, Optional[float]]]:
    """Coerce retriever outputs into (Document, score) tuples."""
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


def _format_documents(
    docs: Sequence[Tuple[Document, Optional[float]]],
    *,
    max_documents: int,
    max_chars: int,
) -> str:
    """Render retrieved documents into a compact string."""
    if not docs:
        return "No documents found in the knowledge base for this query."

    snippets = []
    for idx, (doc, score) in enumerate(docs[:max_documents], start=1):
        source = (
            doc.metadata.get("source")
            or doc.metadata.get("filename")
            or doc.metadata.get("path")
            or "unknown source"
        )
        text = doc.page_content.strip()
        if len(text) > max_chars:
            text = f"{text[:max_chars].rstrip()}..."
        header = f"[{idx}] {source}"
        footer = f"Score: {score:.4f}" if isinstance(score, (float, int)) else "Score: n/a"
        snippets.append(f"{header}\n{footer}\n{text}")

    return "\n\n".join(snippets)


def create_retriever_tool(
    retriever: BaseRetriever,
    *,
    name: str = "search_knowledge_base",
    description: Optional[str] = None,
    max_documents: int = 4,
    max_chars: int = 800,
) -> Callable[[str], str]:
    """
    Wrap a `BaseRetriever` instance in a LangChain tool.

    The resulting tool returns a formatted string combining the retrieved documents
    so the calling agent can ground its responses in the vector store content.
    """

    tool_description = (
        description
        or "Use this tool to search the indexed knowledge base and return the most relevant passages."
    )

    @tool(name, description=tool_description)
    def _retriever_tool(query: str) -> str:
        results = retriever.invoke(query)
        docs = _normalize_results(results or [])
        return _format_documents(docs, max_documents=max_documents, max_chars=max_chars)

    return _retriever_tool


# ---------------------------------------------------------------------------
# Catalog-backed local tools
# ---------------------------------------------------------------------------

DEFAULT_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".rst",
    ".pdf",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
    ".log",
}


@dataclass
class CatalogService:
    """Expose lightweight access to catalogued resources and metadata."""
    # TODO should this be put in the index_utils instead? seems it might be of general use

    data_path: Path | str
    include_extensions: Sequence[str] = field(default_factory=lambda: sorted(DEFAULT_TEXT_EXTENSIONS))
    _file_index: Dict[str, str] = field(init=False, default_factory=dict)
    _metadata_index: Dict[str, str] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.data_path = Path(self.data_path)
        if self.include_extensions:
            self.include_extensions = tuple(ext.lower() for ext in self.include_extensions)
        self.refresh()

    def refresh(self) -> None:
        """Reload file and metadata indices from disk."""
        logger.debug("Refreshing catalog indices from %s", self.data_path)
        self._file_index = load_sources_catalog(self.data_path)
        self._metadata_index = load_index(self.data_path, filename="metadata_index.yaml")

    @property
    def file_index(self) -> Dict[str, str]:
        return self._file_index

    @property
    def metadata_index(self) -> Dict[str, str]:
        return self._metadata_index

    def iter_files(self) -> Iterable[Tuple[str, Path]]:
        for resource_hash, absolute_path in self._file_index.items():
            path = Path(absolute_path)
            if not path.exists():
                continue
            if self.include_extensions and path.suffix.lower() not in self.include_extensions:
                continue
            yield resource_hash, path

    def metadata_path_for(self, resource_hash: str) -> Optional[Path]:
        stored = self._metadata_index.get(resource_hash)
        if not stored:
            return None
        metadata_path = Path(stored)
        if not metadata_path.is_absolute():
            metadata_path = (self.data_path / metadata_path).resolve()
        return metadata_path if metadata_path.exists() else None


def _extract_text(path: Path) -> Optional[str]:
    """Return a text representation of the target file, if possible."""
    suffix = path.suffix.lower()
    try:
        if suffix in {".txt", ".md", ".rst", ".log"}:
            return path.read_text(encoding="utf-8", errors="ignore")
        if suffix in {".json", ".yaml", ".yml", ".csv", ".tsv"}:
            return path.read_text(encoding="utf-8", errors="ignore")
        if suffix in {".html", ".htm"}:
            return path.read_text(encoding="utf-8", errors="ignore")
        if suffix == ".pdf":
            try:
                from pypdf import PdfReader  # type: ignore
            except ImportError:
                logger.warning("pypdf not installed; skipping PDF %s", path)
                return None
            reader = PdfReader(str(path))
            text_parts: List[str] = []
            for page in reader.pages:
                text_parts.append(page.extract_text() or "")
            return "\n".join(text_parts)
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None
    return None


def _format_file_hits(hits: List[Tuple[str, Path, str]]) -> str:
    if not hits:
        return "No local files matched that search query."
    lines: List[str] = []
    for idx, (resource_hash, path, snippet) in enumerate(hits, start=1):
        lines.append(
            f"[{idx}] {path} (hash={resource_hash})\nSnippet:\n{snippet.strip()}"
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
) -> Callable[[str], str]:
    """Create a LangChain tool that performs keyword search in catalogued files."""

    tool_description = (
        description
        or "Search the locally stored source documents (text, markdown, csv, html, pdf)."
    )

    @tool(name, description=tool_description)
    def _search_local_files(query: str) -> str:
        if not query.strip():
            return "Please provide a non-empty search query."

        pattern = re.compile(re.escape(query.strip()), re.IGNORECASE)
        hits: List[Tuple[str, Path, str]] = []

        for resource_hash, path in catalog.iter_files():
            text = _extract_text(path)
            if not text:
                continue
            match = pattern.search(text)
            if not match:
                continue
            snippet = _collect_snippet(text, match)
            hits.append((resource_hash, path, snippet))
            if len(hits) >= max_results:
                break

        return _format_file_hits(hits)

    return _search_local_files


def _format_metadata_hits(results: List[Tuple[str, Path, Dict[str, str]]]) -> str:
    if not results:
        return "No metadata entries matched that search query."

    formatted: List[str] = []
    for idx, (resource_hash, path, matches) in enumerate(results, start=1):
        lines = [f"[{idx}] metadata hash={resource_hash} file={path}"]
        for key, value in matches.items():
            lines.append(f"- {key}: {value}")
        formatted.append("\n".join(lines))
    return "\n\n".join(formatted)


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

        results: List[Tuple[str, Path, Dict[str, str]]] = []
        query_lower = query.lower()

        for resource_hash, _ in catalog.metadata_index.items():
            metadata_path = catalog.metadata_path_for(resource_hash)
            if not metadata_path:
                continue
            try:
                import yaml

                with metadata_path.open("r", encoding="utf-8") as fh:
                    parsed = yaml.safe_load(fh) or {}
            except Exception as exc:
                logger.warning("Failed to load metadata %s: %s", metadata_path, exc)
                continue

            if not isinstance(parsed, dict):
                continue

            flattened = _flatten_metadata(parsed)
            matches = {
                key: value
                for key, value in flattened.items()
                if query_lower in key.lower() or query_lower in value.lower()
            }
            if matches:
                results.append((resource_hash, metadata_path, matches))
                if len(results) >= max_results:
                    break

        return _format_metadata_hits(results)

    return _search_metadata


__all__ = [
    "create_retriever_tool",
    "CatalogService",
    "create_file_search_tool",
    "create_metadata_search_tool",
]
