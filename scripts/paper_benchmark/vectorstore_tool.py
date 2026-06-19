"""Mint a search_vectorstore_hybrid tool that calls the data-manager's
catalog API with mode=hybrid — matches the gold-tier RAG config's
search behavior.

Bypasses RemoteCatalogClient.search() because that method auto-adds
grep-specific query params (regex, case_sensitive, before, after) that
the hybrid endpoint interprets as filters and returns zero hits. The
bare /api/catalog/search with just q+limit+mode returns the expected
hybrid result set."""
import json
import os
import asyncio
from pathlib import Path
from urllib.parse import urlparse

import requests
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - ORCD image has httpx
    httpx = None

# Corrected ORCD/vLLM runs use the same Postgres/pgvector hybrid retrieval
# backend as QAPipeline. The older HTTP fallback is kept only for diagnostics
# when a Postgres endpoint is not available.


class HybridSearchArgs(BaseModel):
    query: str = Field(..., description="The search query (natural language; BM25 + semantic embeddings used together)")
    limit: int = Field(5, description="Max results to return (default 5)")


def make_search_vectorstore_hybrid(catalog_client, *, max_documents: int = 4, max_chars: int = 800):
    """Mint a search_vectorstore_hybrid tool. Mirrors the gold-tier
    `search_vectorstore_hybrid` from src/archi/.../retriever.py: takes a
    natural-language query, runs hybrid (BM25 + embeddings) retrieval,
    returns formatted snippets."""
    description = (
        "Search the CMS CompOps document corpus (Jira tickets + documentation) "
        "using BM25 + semantic vector embeddings (hybrid). Use this for "
        "paraphrased / conceptual queries where you don't know the exact wording. "
        "Returns up to `limit` document snippets with source filename and a relevance score."
    )

    def _pg_password():
        if os.environ.get("PG_PASSWORD"):
            return os.environ["PG_PASSWORD"]
        secrets_dir = Path(os.environ.get("ARCHI_SECRETS_DIR", os.path.expanduser("~/.archi-bundle-state/bundle/secrets/archi")))
        f = secrets_dir / "pg_password.txt"
        return f.read_text().strip() if f.exists() else None

    def _pg_config():
        url = os.environ.get("ARCHI_POSTGRES_URL")
        if url:
            parsed = urlparse(url)
            return {
                "host": parsed.hostname or "127.0.0.1",
                "port": parsed.port or 5436,
                "user": parsed.username or "archi",
                "dbname": parsed.path.lstrip("/") or "archi-db",
                "password": _pg_password(),
                "connect_timeout": 10,
            }
        if os.environ.get("PGHOST"):
            return {
                "host": os.environ.get("PGHOST", "127.0.0.1"),
                "port": int(os.environ.get("PGPORT", "5436")),
                "user": os.environ.get("PGUSER", "archi"),
                "dbname": os.environ.get("PGDATABASE", "archi-db"),
                "password": _pg_password(),
                "connect_timeout": 10,
            }
        return None

    def _okg_enabled() -> bool:
        return (
            os.environ.get("ARCHI_KNOWLEDGE_BACKEND") == "okg"
            or os.environ.get("BENCHMARK_TIER") == "orcd-vllm-okg"
        )

    def _okg_vectorstore():
        from src.archi.utils.okg_vectorstore import OKGVectorStore, okg_config_from_env

        return OKGVectorStore(okg_config_from_env())

    _vectorstore = None

    def _get_vectorstore():
        nonlocal _vectorstore
        if _vectorstore is not None:
            return _vectorstore
        if _okg_enabled():
            _vectorstore = _okg_vectorstore()
            return _vectorstore
        cfg = _pg_config()
        if not cfg:
            return None
        from langchain_huggingface import HuggingFaceEmbeddings
        from src.data_manager.vectorstore.postgres_vectorstore import PostgresVectorStore

        embedding = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        _vectorstore = PostgresVectorStore(
            pg_config=cfg,
            embedding_function=embedding,
            collection_name=os.environ.get("ARCHI_VECTOR_COLLECTION", "default_collection_with_HuggingFaceEmbeddings"),
            distance_metric="cosine",
        )
        return _vectorstore

    def _headers():
        headers = dict(getattr(catalog_client, "_headers", {}) or {})
        # The benchmark fires many short-lived catalog calls from cancellation-
        # heavy agent loops. Avoid HTTP keep-alive so abandoned calls do not
        # leave a larger pool of half-closed sockets on the Flask dev server.
        headers.setdefault("Connection", "close")
        return headers

    def _request_timeout() -> float:
        configured = float(getattr(catalog_client, "timeout", 20.0) or 20.0)
        cap = float(os.environ.get("CATALOG_HTTP_TIMEOUT_S", "20"))
        return max(1.0, min(configured, cap))

    def _hybrid_call(query: str, limit: int = 5):
        # Bare HTTP call with only q/limit/mode — avoids the grep-specific
        # params RemoteCatalogClient.search() adds.
        resp = requests.get(
            f"{catalog_client.base_url}/api/catalog/search",
            params={"q": query, "limit": limit or max_documents, "mode": "hybrid"},
            headers=_headers(),
            timeout=_request_timeout(),
            allow_redirects=False,
        )
        resp.raise_for_status()
        return resp.json().get("hits", []) or []

    async def _hybrid_call_async(query: str, limit: int = 5):
        # This must stay genuinely async. A prior implementation called
        # requests.get() inside the coroutine, which blocked the event loop and
        # prevented asyncio.wait_for() from enforcing per-tool timeouts.
        if httpx is None:
            return await asyncio.to_thread(_hybrid_call, query, limit)

        timeout_s = _request_timeout()
        timeout = httpx.Timeout(
            timeout_s,
            connect=min(5.0, timeout_s),
            read=timeout_s,
            write=min(5.0, timeout_s),
            pool=min(5.0, timeout_s),
        )
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            resp = await client.get(
                f"{catalog_client.base_url}/api/catalog/search",
                params={"q": query, "limit": limit or max_documents, "mode": "hybrid"},
                headers=_headers(),
            )
        resp.raise_for_status()
        return resp.json().get("hits", []) or []

    def _format_hits(hits):
        if not hits:
            return "No documents found in the catalog for that query."
        lines = []
        for i, h in enumerate(hits, start=1):
            if isinstance(h, tuple):
                doc, score = h
                md = getattr(doc, "metadata", {}) or {}
                text = getattr(doc, "page_content", "") or ""
            else:
                md = h.get("metadata") or {}
                score = h.get("score")
                text = h.get("snippet") or h.get("text") or h.get("content") or ""
                if not text and h.get("matches"):
                    text = h["matches"][0].get("text", "")
            name = (
                md.get("display_name")
                or md.get("file_name")
                or md.get("filename")
                or md.get("source")
                or md.get("url")
                or md.get("okg_node_id")
                or (h.get("path", "?") if isinstance(h, dict) else "?")
            )
            url = md.get("url", "")
            text = (text[:max_chars] + "...") if len(text) > max_chars else text
            score_str = f"{score:.3f}" if isinstance(score, (int, float)) else "n/a"
            header = f"[{i}] {name}"
            if url: header += f"  ({url})"
            if md.get("okg_generation_id"):
                header += f"  generation={md.get('okg_generation_id')}"
            if md.get("okg_subtype"):
                header += f"  subtype={md.get('okg_subtype')}"
            header += f"  score={score_str}"
            lines.append(f"{header}\n{text}")
        return "\n\n".join(lines)

    def sync_wrapper(query: str, limit: int = 5):
        vectorstore = _get_vectorstore()
        if vectorstore is not None:
            return _format_hits(vectorstore.hybrid_search(query, k=limit or max_documents))
        return _format_hits(_hybrid_call(query, limit))

    async def async_wrapper(query: str, limit: int = 5):
        vectorstore = _get_vectorstore()
        if vectorstore is not None:
            return _format_hits(await asyncio.to_thread(vectorstore.hybrid_search, query, limit or max_documents))
        return _format_hits(await _hybrid_call_async(query, limit))

    return StructuredTool.from_function(
        func=sync_wrapper,
        coroutine=async_wrapper,
        name="search_vectorstore_hybrid",
        description=description,
        args_schema=HybridSearchArgs,
    )
