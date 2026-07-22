"""Agent tools for reading the current conversation's Attachments (spec 2026-07-07).

Read-only, conversation-scoped: each factory closes over an
AttachmentToolContext, so no tool argument can name another conversation.
"""
from __future__ import annotations

from typing import Callable, Optional

from langchain.tools import tool

from src.utils.attachment_reader import AttachmentToolContext, list_files, read_file, search_files
from src.utils.logging import get_logger

logger = get_logger(__name__)


def create_attachment_list_tool(ctx: AttachmentToolContext, *, name: str = "list_attachment_files",
                                store_tool_input: Optional[Callable[[str, dict], None]] = None) -> Callable:
    description = (
        "List every file the user attached to this conversation, including all "
        "entries inside zip bundles (name, size, readability). Use this first "
        "when an attachment is marked MANIFEST ONLY. The output is the COMPLETE "
        "path tree with byte sizes; sizes are authoritative (0 B = an empty file "
        "— no need to read it); use prefix to scope to a folder."
    )

    @tool(name, description=description)
    def _list_tool(prefix: str = "", glob: str = "") -> str:
        if store_tool_input:
            try:
                store_tool_input(name, {"prefix": prefix, "glob": glob})
            except Exception:
                logger.debug("Failed to store runtime input for tool '%s'", name, exc_info=True)
        try:
            return list_files(ctx, prefix=prefix, glob=glob)
        except Exception as exc:
            logger.warning("%s failed: %s", name, exc)
            return "Listing attachments failed unexpectedly."

    return _list_tool


def create_attachment_read_tool(ctx: AttachmentToolContext, *, name: str = "read_attachment_file",
                                store_tool_input: Optional[Callable[[str, dict], None]] = None) -> Callable:
    description = (
        "Read an attached file's text. For plain attachments pass filename only; "
        "for a file inside a zip bundle pass filename plus entry (the path from "
        "list_attachment_files). Long files are returned in slices — continue "
        "with the offset value the previous call reported. mode=\"raw\" returns "
        "the original stored text exactly (including HTML <script>/<style> "
        "content) — use it when extraction seems partial or a file reads emptier "
        "than its size suggests."
    )

    @tool(name, description=description)
    def _read_tool(filename: str, entry: str = "", offset: int = 0, mode: str = "text") -> str:
        if store_tool_input:
            try:
                store_tool_input(name, {"filename": filename, "entry": entry, "offset": offset, "mode": mode})
            except Exception:
                logger.debug("Failed to store runtime input for tool '%s'", name, exc_info=True)
        try:
            return read_file(ctx, filename, entry=entry, offset=offset, mode=mode)
        except Exception as exc:
            logger.warning("%s failed: %s", name, exc)
            return "Reading the attachment failed unexpectedly."

    return _read_tool


def create_attachment_search_tool(ctx: AttachmentToolContext, *, name: str = "search_attachment_files",
                                  store_tool_input: Optional[Callable[[str, dict], None]] = None) -> Callable:
    description = (
        "Search (grep) across all attached files in this conversation, including "
        "inside zip bundles; matches file contents AND file names. Returns "
        "file:line matches. Set regex=true for a regular-expression query."
    )

    @tool(name, description=description)
    def _search_tool(query: str, regex: bool = False) -> str:
        if store_tool_input:
            try:
                store_tool_input(name, {"query": query, "regex": regex})
            except Exception:
                logger.debug("Failed to store runtime input for tool '%s'", name, exc_info=True)
        try:
            return search_files(ctx, query, regex=regex)
        except Exception as exc:
            logger.warning("%s failed: %s", name, exc)
            return "Searching attachments failed unexpectedly."

    return _search_tool
