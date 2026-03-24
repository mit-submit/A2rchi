"""MONIT OpenSearch search and aggregation tools for the Copilot SDK.

Migrated from ``src.archi.pipelines.agents.tools.monit_opensearch``.
The ``MONITOpenSearchClient`` and response formatters are imported from
the original module to avoid duplicating HTTP / formatting code.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.archi.pipelines.agents.tools.monit_opensearch import (
    MAX_RESULTS_HARD_LIMIT,
    MONITOpenSearchClient,
    _format_aggregation_response,
    _format_opensearch_response,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


# ── Pydantic input models ────────────────────────────────────────────────

class OpenSearchSearchInput(BaseModel):
    query: str = Field(description="Lucene query string.")
    from_time: str = Field(default="now-24h", description="Start time (date math).")
    to_time: str = Field(default="now", description="End time (date math).")
    max_results: int = Field(default=10, description="Max documents to return.")


class OpenSearchAggregationInput(BaseModel):
    query: str = Field(description="Lucene query string to filter documents.")
    group_by: str = Field(description="Field to aggregate on.")
    agg_type: str = Field(default="terms", description="Aggregation type: terms, sum, avg, min, max, cardinality.")
    top_n: int = Field(default=10, description="Number of top buckets for terms aggregation.")
    from_time: str = Field(default="now-24h", description="Start time (date math).")
    to_time: str = Field(default="now", description="End time (date math).")


# ── Tool metadata for registry ───────────────────────────────────────────

SEARCH_TOOL_NAME = "monit_opensearch_search"
SEARCH_TOOL_DESCRIPTION = "Search MONIT OpenSearch for CMS Rucio events."

AGGREGATION_TOOL_NAME = "monit_opensearch_aggregation"
AGGREGATION_TOOL_DESCRIPTION = "Run aggregation queries on MONIT OpenSearch for CMS Rucio events."


# ── Factory functions ────────────────────────────────────────────────────

def build_monit_search_tool(
    client: MONITOpenSearchClient,
    *,
    tool_name: str = SEARCH_TOOL_NAME,
    index: str,
    skill: Optional[str] = None,
):
    from copilot import define_tool

    # Build description, optionally appending domain skill
    base_desc = (
        f"Search the '{index}' OpenSearch index using Lucene query syntax.\n\n"
        "Input parameters:\n"
        "- query: Lucene query string (required).\n"
        "- from_time: Start time (default: 'now-24h'). Supports date math.\n"
        "- to_time: End time (default: 'now'). Supports date math.\n"
        f"- max_results: Max documents to return (default: 10, hard limit: {MAX_RESULTS_HARD_LIMIT}).\n"
    )
    if skill:
        base_desc += f"\n--- Domain Knowledge ---\n{skill}"

    @define_tool(name=tool_name, description=base_desc)
    async def _search_opensearch(params: OpenSearchSearchInput) -> str:
        query = params.query
        from_time = params.from_time
        to_time = params.to_time
        max_results = params.max_results
        if not query or not query.strip():
            return "Please provide a non-empty Lucene query."

        effective_max = min(max_results, MAX_RESULTS_HARD_LIMIT)

        try:
            import requests as req_lib

            response = client.search_with_lucene(
                lucene_query=query.strip(),
                from_time=from_time,
                to_time=to_time,
                size=effective_max,
                index=index,
            )
            return _format_opensearch_response(
                response, query.strip(), index, effective_max,
                from_time=from_time, to_time=to_time,
            )
        except Exception as e:
            logger.error("OpenSearch query error: %s", e, exc_info=True)
            return f"Error querying OpenSearch: {e}"

    return _search_opensearch


def build_monit_aggregation_tool(
    client: MONITOpenSearchClient,
    *,
    tool_name: str = AGGREGATION_TOOL_NAME,
    index: str,
    skill: Optional[str] = None,
):
    from copilot import define_tool

    base_desc = (
        f"Run aggregation queries on the '{index}' OpenSearch index.\n\n"
        "Use this for counting, grouping, statistics — NOT for fetching individual documents.\n\n"
        "Input parameters:\n"
        "- query: Lucene query string to filter documents (required). Use '*' for all.\n"
        "- group_by: Field to aggregate on (required).\n"
        "- agg_type: Aggregation type (default: 'terms'). One of: terms, sum, avg, min, max, cardinality.\n"
        "- top_n: Number of top buckets for terms aggregation (default: 10, max: 100).\n"
        "- from_time: Start time (default: 'now-24h'). Supports date math.\n"
        "- to_time: End time (default: 'now'). Supports date math.\n"
    )
    if skill:
        base_desc += f"\n--- Domain Knowledge ---\n{skill}"

    @define_tool(name=tool_name, description=base_desc)
    async def _aggregate_opensearch(params: OpenSearchAggregationInput) -> str:
        query = params.query
        group_by = params.group_by
        agg_type = params.agg_type
        top_n = params.top_n
        from_time = params.from_time
        to_time = params.to_time
        if not query or not query.strip():
            return "Please provide a non-empty Lucene query (use '*' for all documents)."
        if not group_by or not group_by.strip():
            return "Please provide a field to aggregate on (group_by)."

        try:
            response = client.search_with_aggregation(
                lucene_query=query.strip(),
                group_by=group_by.strip(),
                agg_type=agg_type,
                top_n=top_n,
                from_time=from_time,
                to_time=to_time,
                index=index,
            )
            return _format_aggregation_response(
                response, query.strip(), index, group_by.strip(), agg_type,
                from_time=from_time, to_time=to_time,
            )
        except Exception as e:
            logger.error("OpenSearch aggregation error: %s", e, exc_info=True)
            return f"Error running aggregation: {e}"

    return _aggregate_opensearch
