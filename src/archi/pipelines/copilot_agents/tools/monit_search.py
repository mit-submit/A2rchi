"""MONIT OpenSearch search and aggregation tools for the Copilot SDK.

Migrated from ``src.archi.pipelines.agents.tools.monit_opensearch``.
The ``MONITOpenSearchClient`` and response formatters are imported from
the original module to avoid duplicating HTTP / formatting code.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from src.archi.pipelines.agents.tools.monit_opensearch import (
    DEFAULT_MAX_RESULTS, MAX_RESULTS_HARD_LIMIT, MONITOpenSearchClient,
    _format_aggregation_response, _format_fetch_response,
    _format_opensearch_response, _get_summary_fields)
from src.utils.logging import get_logger

logger = get_logger(__name__)


# ── Pydantic input models ────────────────────────────────────────────────


class OpenSearchSearchInput(BaseModel):
    query: str = Field(description="Lucene query string.")
    from_time: str = Field(default="now-24h", description="Start time (date math).")
    to_time: str = Field(default="now", description="End time (date math).")
    max_results: int = Field(default=DEFAULT_MAX_RESULTS, description="Max documents per page.")
    page: int = Field(default=1, description="Page number for pagination (default: 1).")


class OpenSearchAggregationInput(BaseModel):
    query: str = Field(description="Lucene query string to filter documents.")
    group_by: str = Field(description="Field to aggregate on.")
    agg_type: str = Field(
        default="terms",
        description="Aggregation type: terms, sum, avg, min, max, cardinality.",
    )
    top_n: int = Field(
        default=10, description="Number of top buckets for terms aggregation."
    )
    from_time: str = Field(default="now-24h", description="Start time (date math).")
    to_time: str = Field(default="now", description="End time (date math).")


class OpenSearchFetchInput(BaseModel):
    document_id: str = Field(description="The OpenSearch document _id from search results.")


# ── Tool metadata for registry ───────────────────────────────────────────

SEARCH_TOOL_NAME = "monit_opensearch_search"
SEARCH_TOOL_DESCRIPTION = "Search MONIT OpenSearch for CMS Rucio events."

AGGREGATION_TOOL_NAME = "monit_opensearch_aggregation"
AGGREGATION_TOOL_DESCRIPTION = (
    "Run aggregation queries on MONIT OpenSearch for CMS Rucio events."
)

CONDOR_SEARCH_TOOL_NAME = "condor_opensearch_search"
CONDOR_SEARCH_TOOL_DESCRIPTION = "Search MONIT OpenSearch for CMS HTCondor job metrics."

CONDOR_AGGREGATION_TOOL_NAME = "condor_opensearch_aggregation"
CONDOR_AGGREGATION_TOOL_DESCRIPTION = (
    "Run aggregation queries on MONIT OpenSearch for CMS HTCondor job metrics."
)


# ── Factory functions ────────────────────────────────────────────────────


def build_monit_search_tool(
    client: MONITOpenSearchClient,
    *,
    tool_name: str = SEARCH_TOOL_NAME,
    index: str,
    skill: Optional[str] = None,
):
    from copilot import define_tool

    summary_fields = _get_summary_fields(index)

    # Build description, optionally appending domain skill
    base_desc = (
        f"Search the '{index}' OpenSearch index using Lucene query syntax.\n"
        "Returns compact summaries of matching documents with key fields only.\n\n"
        "IMPORTANT:\n"
        "- For counting or statistics, prefer the aggregation tool instead.\n"
        "- For full document details, use the fetch tool with a document ID from the results.\n"
        "- Start with a narrow query and small result set; expand if needed.\n\n"
        "Input parameters:\n"
        "- query: Lucene query string (required).\n"
        "- from_time: Start time (default: 'now-24h'). Supports date math.\n"
        "- to_time: End time (default: 'now'). Supports date math.\n"
        f"- max_results: Max documents per page (default: {DEFAULT_MAX_RESULTS}, hard limit: {MAX_RESULTS_HARD_LIMIT}).\n"
        "- page: Page number for pagination (default: 1). Use page=2 to see the next batch.\n"
    )
    if skill:
        base_desc += f"\n--- Domain Knowledge ---\n{skill}"

    @define_tool(name=tool_name, description=base_desc)
    async def _search_opensearch(params: OpenSearchSearchInput) -> str:
        query = params.query
        from_time = params.from_time
        to_time = params.to_time
        max_results = params.max_results
        page = max(1, params.page)
        if not query or not query.strip():
            return "Please provide a non-empty Lucene query."

        effective_max = min(max_results, MAX_RESULTS_HARD_LIMIT)
        offset = (page - 1) * effective_max

        try:
            response = client.search_with_lucene(
                lucene_query=query.strip(),
                from_time=from_time,
                to_time=to_time,
                size=effective_max,
                offset=offset,
                index=index,
            )
            return _format_opensearch_response(
                response,
                query.strip(),
                index,
                effective_max,
                from_time=from_time,
                to_time=to_time,
                summary_fields=summary_fields,
                page=page,
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
        "Use this for counting, grouping, statistics — NOT for fetching individual documents.\n"
        "PREFER this tool over the search tool when the question asks about counts, distributions,\n"
        "top errors, totals, averages, or any summary statistics.\n\n"
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
            return (
                "Please provide a non-empty Lucene query (use '*' for all documents)."
            )
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
                response,
                query.strip(),
                index,
                group_by.strip(),
                agg_type,
                from_time=from_time,
                to_time=to_time,
            )
        except Exception as e:
            logger.error("OpenSearch aggregation error: %s", e, exc_info=True)
            return f"Error running aggregation: {e}"

    return _aggregate_opensearch


def build_monit_fetch_tool(
    client: MONITOpenSearchClient,
    *,
    tool_name: str = "fetch_monit_document",
    index: str,
):
    from copilot import define_tool

    base_desc = (
        f"Fetch the full details of a single document from the '{index}' OpenSearch index by its ID.\n\n"
        "Use this tool AFTER searching to retrieve the complete contents of a specific document.\n"
        "The document_id comes from search results (shown as id=<value> in each result row).\n\n"
        "Input parameters:\n"
        "- document_id: The OpenSearch document _id (required).\n"
    )

    @define_tool(name=tool_name, description=base_desc)
    async def _fetch_document(params: OpenSearchFetchInput) -> str:
        document_id = params.document_id
        if not document_id or not document_id.strip():
            return "Please provide a document_id."

        try:
            response = client.get_document_by_id(
                document_id=document_id.strip(),
                index=index,
            )
            return _format_fetch_response(response, document_id.strip(), index)
        except Exception as e:
            logger.error("Fetch error for doc %s: %s", document_id, e, exc_info=True)
            return f"Error fetching document: {e}"

    return _fetch_document


# ── Condor convenience wrappers ──────────────────────────────────────────
# Reuse the generic factories with condor-specific defaults.


def build_condor_search_tool(
    client: MONITOpenSearchClient,
    *,
    tool_name: str = CONDOR_SEARCH_TOOL_NAME,
    index: str = "monit_prod_condor_raw_metric*",
    skill: Optional[str] = None,
):
    return build_monit_search_tool(
        client, tool_name=tool_name, index=index, skill=skill
    )


def build_condor_aggregation_tool(
    client: MONITOpenSearchClient,
    *,
    tool_name: str = CONDOR_AGGREGATION_TOOL_NAME,
    index: str = "monit_prod_condor_raw_metric*",
    skill: Optional[str] = None,
):
    return build_monit_aggregation_tool(
        client, tool_name=tool_name, index=index, skill=skill
    )


def build_condor_fetch_tool(
    client: MONITOpenSearchClient,
    *,
    tool_name: str = "fetch_condor_document",
    index: str = "monit_prod_condor_raw_metric*",
):
    return build_monit_fetch_tool(
        client, tool_name=tool_name, index=index,
    )
