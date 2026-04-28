"""Agent tools for triggering archi-side ingestion of arbitrary URLs.

Wraps the data-manager's ``POST /document_index/upload_url`` endpoint so the
agent can ask archi to scrape and index a URL it has discovered (e.g. an
Indico event URL surfaced by the indico MCP server). The data-manager
auto-routes Indico URLs through ``IndicoScraper`` and falls back to
``LinkScraper`` for everything else, so this tool is URL-agnostic.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

import requests
from langchain_core.tools import tool

from src.archi.pipelines.agents.tools.base import require_tool_permission
from src.utils.logging import get_logger

logger = get_logger(__name__)


def create_ingest_url_tool(
    data_manager_url: str,
    *,
    headers: Optional[Mapping[str, str]] = None,
    name: str = "ingest_url",
    description: Optional[str] = None,
    timeout_seconds: float = 600.0,
    required_permission: Optional[str] = None,
) -> Callable[..., str]:
    """Build a LangChain tool that POSTs a URL to data-manager for ingestion.

    The data-manager dispatches Indico event URLs to ``IndicoScraper`` and
    every other URL to the generic link scraper, so the agent does not need
    to know which scraper applies — just hand it a URL.
    """

    tool_description = description or (
        "Ingest a URL into the knowledge base so it becomes searchable.\n"
        "Use after surfacing a URL via INDICO_get_files (or any source) when "
        "the user wants the page contents (slides, agenda, attached materials) "
        "available to search_vectorstore_hybrid afterwards.\n"
        "Indico event URLs are auto-routed through the Indico scraper "
        "(API + slide-to-markdown conversion); other URLs go through the "
        "generic link scraper.\n"
        "Input: url (string). Optional: depth (int >= 1) for link-scraper crawl depth.\n"
        "Output: a short status string with the number of resources ingested."
    )

    upload_endpoint = f"{data_manager_url.rstrip('/')}/document_index/upload_url"
    request_headers = dict(headers or {})

    @tool(name, description=tool_description)
    @require_tool_permission(required_permission)
    def _ingest_url(url: str, depth: Optional[int] = None) -> str:
        url = (url or "").strip()
        if not url:
            return "Error: please provide a non-empty URL."

        form: dict[str, str] = {"url": url}
        if depth is not None:
            form["depth"] = str(depth)

        try:
            resp = requests.post(
                upload_endpoint,
                data=form,
                headers=request_headers,
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning("ingest_url request to %s failed: %s", upload_endpoint, exc)
            return f"Error: data-manager unreachable at {upload_endpoint}: {exc}"

        if resp.status_code != 200:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text[:500]
            return f"Error: data-manager returned {resp.status_code}: {detail}"

        try:
            payload = resp.json()
        except ValueError:
            return f"Ingested {url} (data-manager returned non-JSON: {resp.text[:200]})"

        count = payload.get("resources_scraped")
        if count is None:
            return f"Ingested {url} ({payload})"
        return f"Ingested {url}: {count} resource(s) scraped and indexed."

    return _ingest_url
