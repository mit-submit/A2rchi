from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from langchain.tools import tool
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

from src.utils.logging import get_logger
from src.utils.env import read_secret

logger = get_logger(__name__)


class RemoteCatalogClient:
    """HTTP client for the data-manager catalog API."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        host_mode: Optional[bool] = None,
        hostname: Optional[str] = None,
        port: int = 7871,
        external_port: Optional[int] = None,
        api_token: Optional[str] = None,
        timeout: float = 10.0,
    ):
        host_mode_flag = self._resolve_host_mode(host_mode)

        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            host = hostname or ("localhost" if host_mode_flag else "data-manager")
            final_port = external_port if host_mode_flag and external_port else port
            self.base_url = f"http://{host}:{final_port}"
        self.timeout = timeout
        self.api_token = api_token.strip() if api_token else None

    @classmethod
    def from_deployment_config(cls, config: Optional[Dict[str, object]]) -> "RemoteCatalogClient":
        """Create a client using the standard A2rchi deployment config structure."""
        cfg = config or {}
        services_cfg = cfg.get("services", {}) if isinstance(cfg, dict) else {}
        data_manager_cfg = services_cfg.get("data_manager", {}) if isinstance(services_cfg, dict) else {}
        auth_cfg = data_manager_cfg.get("auth", {}) if isinstance(data_manager_cfg, dict) else {}
        api_token = cls._resolve_api_token(auth_cfg.get("api_token") if isinstance(auth_cfg, dict) else None)

        return cls(
            base_url=data_manager_cfg.get("base_url"),
            host_mode=cfg.get("host_mode"),
            hostname=data_manager_cfg.get("hostname") or data_manager_cfg.get("host"),
            port=data_manager_cfg.get("port", 7871),
            external_port=data_manager_cfg.get("external_port"),
            api_token=api_token,
        )

    @staticmethod
    def _resolve_host_mode(host_mode: Optional[bool]) -> bool:
        if host_mode is None:
            env_host_mode = (
                os.environ.get("HOST_MODE")
                or os.environ.get("HOSTMODE")
                or os.environ.get("A2RCHI_HOST_MODE")
            )
            return str(env_host_mode).lower() in {"1", "true", "yes", "on"}
        return bool(host_mode)

    @staticmethod
    def _resolve_api_token(config_token: Optional[str]) -> Optional[str]:
        token = (config_token or "").strip()
        if token:
            return token
        secret_token = read_secret("DM_API_TOKEN")
        if secret_token:
            return secret_token
        return None

    def _headers(self) -> Dict[str, str]:
        if not self.api_token:
            return {}
        return {"Authorization": f"Bearer {self.api_token}"}

    def search(
        self, query: str, *, limit: int = 5, search_content: bool = True
    ) -> List[Dict[str, object]]:
        resp = requests.get(
            f"{self.base_url}/api/catalog/search",
            params={"q": query, "limit": limit, "search_content": str(search_content).lower()},
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("hits", []) or []

    def get_document(self, resource_hash: str, *, max_chars: int = 4000) -> Optional[Dict[str, object]]:
        resp = requests.get(
            f"{self.base_url}/api/catalog/document/{resource_hash}",
            params={"max_chars": max_chars},
            headers=self._headers(),
            timeout=self.timeout,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()


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
    catalog: RemoteCatalogClient,
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

        try:
            results = catalog.search(query.strip(), limit=max_results, search_content=True)
        except Exception as exc:
            logger.warning("Catalog search failed: %s", exc)
            return "Catalog search failed."

        for item in results:
            resource_hash = item.get("hash")
            path = Path(item.get("path", "")) if item.get("path") else Path("")
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            snippet = item.get("snippet") or ""
            hits.append((resource_hash, path, metadata, snippet))

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
    catalog: RemoteCatalogClient,
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


__all__ = [
    "RemoteCatalogClient",
    "create_retriever_tool",
    "create_file_search_tool",
    "create_metadata_search_tool",
]
