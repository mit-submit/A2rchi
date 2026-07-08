"""
Shared catalog-search helpers.

The metadata-query mini-grammar ("key:value tokens, OR groups, free text")
and grep primitives used by both the uploader's document-search endpoints
and the MCP server's archi_search_* tools. One home so the grammar, alias
map and grep semantics cannot drift between interfaces.

Kept dependency-light (re/shlex only) — src.interfaces.chat_app.mcp_sse must
stay importable in environments without the data_manager/langchain stack.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Tuple

from src.utils.logging import get_logger

logger = get_logger(__name__)

# Legacy aliases accepted in metadata queries, normalized to canonical columns.
METADATA_ALIAS_MAP = {
    "resource_type": "source_type",
    "resource_id": "ticket_id",
}

# Static fallback for environments where the catalog module (and its
# langchain dependency) is not importable — see metadata_filter_keys().
_FALLBACK_FILTER_KEYS = [
    "path",
    "file_path",
    "display_name",
    "source_type",
    "url",
    "ticket_id",
    "suffix",
    "size_bytes",
    "original_path",
    "base_path",
    "relative_path",
    "created_at",
    "modified_at",
    "file_modified_at",
    "ingested_at",
]


def metadata_filter_keys() -> List[str]:
    """
    Queryable metadata keys, derived from the canonical catalog column map so
    a new column is automatically filterable everywhere it is searchable.
    """
    try:
        from src.data_manager.collectors.utils.catalog_postgres import _METADATA_COLUMN_MAP
        return sorted(set(_METADATA_COLUMN_MAP))
    except ImportError:
        return sorted(_FALLBACK_FILTER_KEYS)


def parse_metadata_query(query: str) -> Tuple[Dict[str, str] | List[Dict[str, str]], str]:
    """
    Split a search query into metadata filters and free-text terms.

    "key:value" tokens become filters (legacy keys normalized via
    METADATA_ALIAS_MAP), "OR" starts a new filter group, everything else is
    free text. Returns (filters, free_text) where filters is {} (none), a
    single dict (one group), or a list of dicts (OR groups).
    """
    filter_groups: List[Dict[str, str]] = []
    current_group: Dict[str, str] = {}
    free_tokens: List[str] = []

    try:
        tokens = shlex.split(query)
    except ValueError as exc:
        # Fall back to whitespace tokenization for malformed quoted input.
        logger.warning("Invalid metadata query syntax; using fallback tokenization: %s", exc)
        tokens = query.split()

    for token in tokens:
        if token.upper() == "OR":
            if current_group:
                filter_groups.append(current_group)
                current_group = {}
            continue
        if ":" in token:
            key, value = token.split(":", 1)
            key = METADATA_ALIAS_MAP.get(key.strip(), key.strip())
            value = value.strip()
            if key and value:
                current_group[key] = value
                continue
        free_tokens.append(token)

    if current_group:
        filter_groups.append(current_group)

    if not filter_groups:
        filters: Dict[str, str] | List[Dict[str, str]] = {}
    elif len(filter_groups) == 1:
        filters = filter_groups[0]
    else:
        filters = filter_groups

    return filters, " ".join(free_tokens)


def compile_query_pattern(query: str, *, regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = query if regex else re.escape(query)
    return re.compile(pattern, flags)


def grep_text_lines(
    text: str,
    pattern: re.Pattern[str],
    *,
    before: int = 0,
    after: int = 0,
    max_matches: int = 3,
) -> List[Dict[str, Any]]:
    """Return up to max_matches matching lines with optional context lines."""
    if max_matches <= 0:
        return []
    lines = text.splitlines()
    matches: List[Dict[str, Any]] = []
    for idx, line in enumerate(lines):
        if not pattern.search(line):
            continue
        matches.append(
            {
                "line": idx + 1,
                "text": line,
                "before": lines[max(0, idx - before):idx] if before else [],
                "after": lines[idx + 1: idx + 1 + after] if after else [],
            }
        )
        if len(matches) >= max_matches:
            break
    return matches
