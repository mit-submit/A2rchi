"""Agent tool for ingesting an Indico event into the knowledge base.

This is the authenticated counterpart to ``ingest_url``: it ingests files
that have already been downloaded by the Indico MCP container (which holds
the bearer/API credentials) into a shared volume the data-manager can read.

Flow:
  1. The agent first calls ``INDICO_get_files(event_id, download_files=true)``
     — that tool, served by the Indico MCP server, authenticates and saves
     attachments to ``/shared/indico-downloads/{event_id}/``.
  2. The agent then calls ``ingest_indico_event(event_id=..., event_url=...)``
     (this tool), which POSTs to the data-manager's
     ``/document_index/ingest_local_path`` endpoint. The endpoint walks the
     shared directory and ingests every file with metadata.event_id stamped.
  3. The agent retrieves the new content via ``search_metadata_index`` with
     ``event_id:<id>`` followed by ``fetch_catalog_document``.
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

import requests
from langchain_core.tools import tool

from src.archi.pipelines.agents.tools.base import require_tool_permission
from src.utils.logging import get_logger

logger = get_logger(__name__)


def create_ingest_indico_event_tool(
    data_manager_url: str,
    *,
    shared_root: str = "/shared/indico-downloads",
    headers: Optional[Mapping[str, str]] = None,
    name: str = "ingest_indico_event",
    description: Optional[str] = None,
    timeout_seconds: float = 600.0,
    required_permission: Optional[str] = None,
    store_tool_input: Optional[Callable[[str, object], None]] = None,
) -> Callable[..., str]:
    """Build a LangChain tool that ingests an Indico event's downloaded files."""

    tool_description = description or (
        "Ingest an Indico event's attachments into the knowledge base.\n"
        "PREREQUISITE: call `INDICO_get_files(event_id, download_files=true)` "
        "FIRST. That MCP tool authenticates via bearer token and saves files to a "
        "shared volume; this tool then asks the data-manager to ingest that "
        "directory (chunk + embed + index into the catalog).\n"
        "Use this for ANY Indico event URL (`indico.cern.ch/event/<id>/...`) — "
        "do NOT call `ingest_url` on Indico URLs (it cannot authenticate and "
        "stores the SSO login page).\n"
        "Input: event_id (string, required); event_url (string, optional — "
        "stamped as metadata.url); contribution_id (string, optional).\n"
        "Output: status string with the number of resources ingested. After a "
        "successful ingest, retrieve content via `search_metadata_index` with "
        "`event_id:<id>` then `fetch_catalog_document` by hash."
    )

    endpoint = f"{data_manager_url.rstrip('/')}/document_index/ingest_local_path"
    request_headers = dict(headers or {})

    @tool(name, description=tool_description)
    @require_tool_permission(required_permission)
    def _ingest_indico_event(
        event_id: str,
        event_url: Optional[str] = None,
        contribution_id: Optional[str] = None,
    ) -> str:
        event_id = (event_id or "").strip()
        if not event_id:
            return "Error: event_id is required."
        if not event_id.isdigit():
            return f"Error: event_id must be numeric (got: {event_id!r})."

        if store_tool_input:
            try:
                store_tool_input(
                    name,
                    {
                        "event_id": event_id,
                        "event_url": event_url,
                        "contribution_id": contribution_id,
                    },
                )
            except Exception:
                logger.debug("Failed to record tool input for %s", name, exc_info=True)

        directory = f"{shared_root.rstrip('/')}/{event_id}"
        # NB: documents.source_type has a CHECK constraint restricting it to a
        # fixed enum; "indico" is not allowed. Match the existing IndicoScraper
        # convention: source_type="web" plus metadata.scraper="indico".
        # Use target_subdir="indico-mcp" (not "indico") to avoid colliding with
        # the legacy IndicoScraper's data dir (persistence.data_path/"indico")
        # in case that scraper is re-enabled later.
        form: dict[str, str] = {
            "path": directory,
            "source_type": "web",
            "target_subdir": "indico-mcp",
            "scraper": "indico",
            "event_id": event_id,
        }
        if event_url:
            form["url"] = event_url
        if contribution_id:
            form["contribution_id"] = contribution_id

        try:
            resp = requests.post(
                endpoint,
                data=form,
                headers=request_headers,
                timeout=timeout_seconds,
            )
        except requests.RequestException as exc:
            logger.warning("ingest_indico_event request to %s failed: %s", endpoint, exc)
            return f"Error: data-manager unreachable at {endpoint}: {exc}"

        if resp.status_code == 404:
            return (
                f"Error: no files at {directory}. Did you call "
                f"`INDICO_get_files(event_id={event_id!r}, download_files=true)` "
                f"first? The MCP server must download to the shared volume "
                f"before this tool can ingest it."
            )
        if resp.status_code == 403:
            try:
                detail = resp.json().get("detail")
            except ValueError:
                detail = resp.text[:500]
            return (
                f"Error: data-manager refused ingest path (path_not_allowed): "
                f"{detail}. The shared downloads root may be misconfigured."
            )
        if resp.status_code != 200:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text[:500]
            return f"Error: data-manager returned {resp.status_code}: {detail}"

        try:
            payload = resp.json()
        except ValueError:
            return f"Ingested event {event_id} (data-manager returned non-JSON: {resp.text[:200]})"

        count = payload.get("resources_ingested", 0)
        followup = (
            f" To retrieve the new content, call `search_metadata_index` with "
            f"`event_id:{event_id}`, then `fetch_catalog_document` by hash. "
            f"Do NOT loop on `search_vectorstore_hybrid` — exact-match metadata "
            f"lookups are deterministic; vectorstore similarity is not."
        )
        if count == 0:
            return (
                f"Ingested event {event_id}: 0 files found at {directory}. The "
                f"Indico MCP `INDICO_get_files` may not have authenticated or "
                f"the event has no attachments. Check the MCP response."
            )
        return f"Ingested event {event_id}: {count} file(s) indexed from {directory}." + followup

    return _ingest_indico_event
