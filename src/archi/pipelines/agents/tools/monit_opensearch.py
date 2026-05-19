"""
MONIT OpenSearch client and LangChain tool factories for querying OpenSearch indices.

This module provides:
- ``MONITOpenSearchClient``: HTTP client for CERN's MONIT Grafana API.
- ``create_monit_opensearch_search_tool``: Factory for index-agnostic search tools.
- ``create_monit_opensearch_aggregation_tool``: Factory for index-agnostic aggregation tools.

The tool factories are designed to work with **any** OpenSearch index. Domain-specific
knowledge (field names, query patterns, etc.) is injected via *skill* markdown files
that are appended to the tool description so the LLM has rich context.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

import requests
from langchain.tools import tool

from src.utils.logging import get_logger

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_TIME_FORMAT = "strict_date_optional_time||epoch_millis"

# Hard limits to prevent LLM context window overflow
MAX_RESULTS_HARD_LIMIT = 10
MAX_OUTPUT_CHARS = 8_000
DEFAULT_MAX_RESULTS = 5
DEFAULT_PAGE_SIZE = 5

# ── Field projections per index ──────────────────────────────────────────────
# Only these fields are shown in search summaries.  Full documents are
# available via the fetch_monit_document tool.

CONDOR_SUMMARY_FIELDS = [
    "data.Site",
    "data.CMS_JobType",
    "data.Type",
    "data.Status",
    "data.ExitCode",
    "data.CpuEff",
    "data.WallClockHr",
    "data.CpuTimeHr",
    "data.RequestCpus",
    "data.Workflow",
    "data.WMAgent_TaskType",
    "data.ScheddName",
    "data.RecordTime",
]

RUCIO_SUMMARY_FIELDS = [
    "data.event_type",
    "data.scope",
    "data.name",
    "data.src_rse",
    "data.dst_rse",
    "data.rse",
    "data.account",
    "data.reason",
    "data.bytes",
    "data.transfer_id",
    "data.created_at",
    "data.started_at",
    "data.transferred_at",
]

# Map index pattern prefixes to their summary field lists
_INDEX_SUMMARY_FIELDS: Dict[str, list] = {
    "monit_prod_condor_raw_metric": CONDOR_SUMMARY_FIELDS,
    "monit_prod_cms_rucio_raw_events": RUCIO_SUMMARY_FIELDS,
}


# ── Client ───────────────────────────────────────────────────────────────────

class MONITOpenSearchClient:
    """
    HTTP client for querying OpenSearch via CERN's MONIT Grafana API.

    This client handles authentication and query formatting for the
    ``_msearch`` endpoint used by Grafana datasource proxies.
    """

    def __init__(
        self,
        *,
        token: str,
        url: str = "https://monit-grafana.cern.ch/api/datasources/proxy/9269/_msearch",
        timeout: float = 60.0,
    ):
        """
        Initialize the MONIT OpenSearch client.

        Args:
            token: Bearer token for MONIT Grafana API authentication.
            url: Full URL to the ``_msearch`` endpoint.
            timeout: Request timeout in seconds.
        """
        if not token:
            raise ValueError(
                "MONIT Grafana token must be provided."
            )

        self.url = url
        self.timeout = timeout

        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }

    def query(
        self,
        opensearch_query: Dict[str, Any],
        *,
        index: str,
        search_type: str = "query_then_fetch",
    ) -> Dict[str, Any]:
        """
        Execute an OpenSearch query against MONIT.

        Args:
            opensearch_query: OpenSearch Query DSL dictionary.
            index: Index pattern to query.
            search_type: Search type for meta query.

        Returns:
            Raw JSON response from OpenSearch.

        Raises:
            requests.HTTPError: On HTTP errors.
            requests.Timeout: On timeout.
        """
        meta_query = {
            "search_type": search_type,
            "ignore_unavailable": True,
            "index": [index],
        }

        # Format as NDJSON (newline-delimited JSON) for _msearch
        payload = "\n".join([json.dumps(meta_query), json.dumps(opensearch_query)]) + "\n"

        response = requests.post(
            self.url,
            headers=self.headers,
            data=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def search_with_lucene(
        self,
        lucene_query: str,
        *,
        from_time: str = "now-24h",
        to_time: str = "now",
        time_field: str = "metadata.timestamp",
        size: int = 10,
        offset: int = 0,
        index: str,
    ) -> Dict[str, Any]:
        """
        Execute a Lucene query with time range filtering.

        Args:
            lucene_query: Lucene query string (e.g., ``data.name="/store/..."``).
            from_time: Start time in date math (e.g., ``now-7d``, ``now-24h``).
            to_time: End time in date math (e.g., ``now``).
            time_field: Field to use for time range filtering.
            size: Maximum number of results to return.
            offset: Number of results to skip (for pagination).
            index: Index pattern to query.

        Returns:
            Raw JSON response from OpenSearch.
        """
        opensearch_query = {
            "size": size,
            "from": offset,
            "_source": True,
            "query": {
                "bool": {
                    "must": [
                        {
                            "query_string": {
                                "query": lucene_query,
                                "analyze_wildcard": True,
                            }
                        }
                    ],
                    "filter": [
                        {
                            "range": {
                                time_field: {
                                    "gte": from_time,
                                    "lte": to_time,
                                    "format": _TIME_FORMAT,
                                }
                            }
                        }
                    ],
                }
            },
            "sort": [
                {time_field: {"order": "desc"}}
            ],
        }

        return self.query(opensearch_query, index=index)

    def search_with_aggregation(
        self,
        lucene_query: str,
        *,
        group_by: str,
        agg_type: str = "terms",
        top_n: int = 10,
        from_time: str = "now-24h",
        to_time: str = "now",
        time_field: str = "metadata.timestamp",
        index: str,
    ) -> Dict[str, Any]:
        """
        Execute a Lucene query with an aggregation.

        Args:
            lucene_query: Lucene query string to filter documents.
            group_by: Field to aggregate on.
            agg_type: Aggregation type (``terms``, ``sum``, ``avg``,
                      ``min``, ``max``, ``cardinality``).
            top_n: Number of buckets for ``terms`` aggregation (max 100).
            from_time: Start time in date math (e.g., ``now-7d``).
            to_time: End time in date math (e.g., ``now``).
            time_field: Field to use for time range filtering.
            index: Index pattern to query.

        Returns:
            Raw JSON response from OpenSearch.
        """
        top_n = min(top_n, 100)

        # Build aggregation clause
        if agg_type == "terms":
            # For text fields, try .keyword sub-field first
            field = group_by if group_by.endswith(".keyword") else f"{group_by}.keyword"
            agg_clause = {"terms": {"field": field, "size": top_n}}
        elif agg_type == "cardinality":
            agg_clause = {"cardinality": {"field": group_by}}
        elif agg_type in ("sum", "avg", "min", "max"):
            agg_clause = {agg_type: {"field": group_by}}
        else:
            raise ValueError(f"Unsupported aggregation type: {agg_type}")

        opensearch_query = self._build_agg_query(
            lucene_query, agg_clause, time_field, from_time, to_time,
        )

        result = self.query(opensearch_query, index=index)

        # Fallback for terms aggregation: if .keyword returned no buckets
        # (e.g. numeric fields), retry with the raw field name.
        if agg_type == "terms" and not group_by.endswith(".keyword"):
            buckets = (
                result.get("responses", [result])[0]
                .get("aggregations", {})
                .get("result", {})
                .get("buckets", [])
            )
            if not buckets:
                logger.debug(
                    "No buckets for '%s.keyword'; retrying with raw field '%s'",
                    group_by, group_by,
                )
                agg_clause = {"terms": {"field": group_by, "size": top_n}}
                opensearch_query = self._build_agg_query(
                    lucene_query, agg_clause, time_field, from_time, to_time,
                )
                result = self.query(opensearch_query, index=index)

        return result

    @staticmethod
    def _build_agg_query(
        lucene_query: str,
        agg_clause: Dict[str, Any],
        time_field: str,
        from_time: str,
        to_time: str,
    ) -> Dict[str, Any]:
        """Build an aggregation query body."""
        return {
            "size": 0,
            "query": {
                "bool": {
                    "must": [
                        {
                            "query_string": {
                                "query": lucene_query,
                                "analyze_wildcard": True,
                            }
                        }
                    ],
                    "filter": [
                        {
                            "range": {
                                time_field: {
                                    "gte": from_time,
                                    "lte": to_time,
                                    "format": _TIME_FORMAT,
                                }
                            }
                        }
                    ],
                }
            },
            "aggs": {
                "result": agg_clause,
            },
        }

    def get_document_by_id(
        self,
        document_id: str,
        *,
        index: str,
    ) -> Dict[str, Any]:
        """
        Fetch a single document by its ``_id``.

        Uses a search query with ``_id`` filter since the MONIT Grafana
        proxy may not support direct GET ``/<index>/_doc/<id>`` requests.

        Args:
            document_id: The OpenSearch document ``_id``.
            index: Index pattern to query.

        Returns:
            Raw JSON response from OpenSearch.
        """
        opensearch_query = {
            "size": 1,
            "query": {
                "ids": {"values": [document_id]}
            },
        }
        return self.query(opensearch_query, index=index)


# ── Response formatting helpers ──────────────────────────────────────────────


def _get_summary_fields(index: str) -> Optional[list]:
    """Return the summary field list for an index pattern, or None for full dump."""
    for prefix, fields in _INDEX_SUMMARY_FIELDS.items():
        if index.startswith(prefix):
            return fields
    return None


def _extract_nested(source: dict, dotted_key: str) -> Any:
    """Extract a value from a nested dict using dot-separated key (e.g. 'data.Site')."""
    parts = dotted_key.split(".")
    current = source
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _format_opensearch_response(
    response: Dict[str, Any],
    query: str,
    index: str,
    max_results: int,
    from_time: str,
    to_time: str,
    *,
    summary_fields: Optional[list] = None,
    page: int = 1,
) -> str:
    """Format an OpenSearch search response for LLM consumption.

    When *summary_fields* is provided, each document is rendered as a compact
    one-line summary with only the projected fields.  Otherwise falls back to
    the full recursive dump (``_append_fields``).
    """
    logger.debug("Response keys: %s", list(response.keys()))

    # Handle _msearch response format (array of responses)
    responses = response.get("responses", [response])
    if not responses:
        return f"No results found for query: {query}"

    first_response = responses[0]
    logger.debug("First response keys: %s", list(first_response.keys()))

    # Check for errors
    if first_response.get("error"):
        error = first_response["error"]
        error_type = error.get("type", "unknown")
        error_reason = error.get("reason", str(error))
        return f"Query error ({error_type}): {error_reason}\n\nQuery was: {query}"

    hits_obj = first_response.get("hits", {})
    hits = hits_obj.get("hits", [])
    total = hits_obj.get("total", {})

    # Handle different total formats
    if isinstance(total, dict):
        total_count = total.get("value", 0)
        relation = total.get("relation", "eq")
        total_str = f"{total_count}+" if relation == "gte" else str(total_count)
    else:
        total_count = total
        total_str = str(total_count)

    if not hits:
        return (
            f"No documents found in '{index}' matching: {query}\n"
            f"Time window: {from_time} → {to_time}"
        )

    # Pagination info
    start_idx = (page - 1) * max_results + 1
    end_idx = start_idx + min(len(hits), max_results) - 1

    # Format header
    lines = [
        f"Found {total_str} document(s) in '{index}' matching: {query}",
        f"Time window: {from_time} → {to_time}",
        f"Showing results {start_idx}-{end_idx} of {total_str} (page {page}):",
        "",
    ]

    # Format each hit
    for idx, hit in enumerate(hits[:max_results], start=start_idx):
        source = hit.get("_source", {})
        doc_id = hit.get("_id", "?")

        # Handle case where _source is a JSON string
        if isinstance(source, str):
            try:
                source = json.loads(source)
            except json.JSONDecodeError:
                lines.append(f"[{idx}] id={doc_id}  Error: Could not parse document data")
                continue

        if not isinstance(source, dict):
            lines.append(f"[{idx}] id={doc_id}  Error: Unexpected document data format")
            continue

        if summary_fields:
            # Compact one-line summary with projected fields
            parts = [f"id={doc_id}"]
            for field in summary_fields:
                value = _extract_nested(source, field)
                if value is not None:
                    # Use short field name (last component)
                    short_name = field.rsplit(".", 1)[-1]
                    str_val = str(value)
                    if len(str_val) > 120:
                        str_val = str_val[:120] + "…"
                    parts.append(f"{short_name}={str_val}")
            lines.append(f"[{idx}] {' | '.join(parts)}")
        else:
            # Full recursive dump (legacy behavior)
            score = hit.get("_score")
            lines.append(f"[{idx}] Document id={doc_id} (score: {score})")
            lines.append("─" * 50)
            _append_fields(lines, source, indent=2)
            lines.append("")

    # Pagination hint
    if total_count > end_idx:
        lines.append("")
        lines.append(f"More results available. Call with page={page + 1} to see the next page.")

    # Fetch hint when using summary fields
    if summary_fields:
        lines.append("")
        lines.append("Use fetch_monit_document(document_id=<id>, index=<index>) for full document details.")

    output = "\n".join(lines)

    # Truncate if output is too large
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n\n... [OUTPUT TRUNCATED - too many results]"

    return output


def _append_fields(
    lines: list,
    obj: Any,
    *,
    indent: int = 0,
    prefix: str = "",
    max_depth: int = 4,
) -> None:
    """Recursively append flattened key-value pairs from a dict/list."""
    if max_depth <= 0:
        return
    pad = " " * indent
    if isinstance(obj, dict):
        for key, value in obj.items():
            full_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
            if isinstance(value, dict):
                _append_fields(lines, value, indent=indent, prefix=full_key, max_depth=max_depth - 1)
            elif isinstance(value, list):
                if len(value) <= 5 and all(not isinstance(v, (dict, list)) for v in value):
                    lines.append(f"{pad}{full_key}: {value}")
                else:
                    lines.append(f"{pad}{full_key}: [{len(value)} items]")
            else:
                str_val = str(value)
                if len(str_val) > 200:
                    str_val = str_val[:200] + "..."
                lines.append(f"{pad}{full_key}: {str_val}")


def _format_aggregation_response(
    response: Dict[str, Any],
    query: str,
    index: str,
    group_by: str,
    agg_type: str,
    from_time: str,
    to_time: str,
) -> str:
    """Format an OpenSearch aggregation response for LLM consumption."""
    responses = response.get("responses", [response])
    if not responses:
        return f"No aggregation results for query: {query}"

    first_response = responses[0]

    if first_response.get("error"):
        error = first_response["error"]
        error_type = error.get("type", "unknown")
        error_reason = error.get("reason", str(error))
        return f"Aggregation error ({error_type}): {error_reason}\n\nQuery was: {query}"

    total = first_response.get("hits", {}).get("total", {})
    if isinstance(total, dict):
        total_count = total.get("value", 0)
    else:
        total_count = total

    agg_result = first_response.get("aggregations", {}).get("result", {})

    lines = [
        f"Aggregation on '{index}' — {agg_type}({group_by})",
        f"Filter: {query}",
        f"Time window: {from_time} → {to_time}",
        f"Total matching documents: {total_count}",
        "",
    ]

    if agg_type == "terms":
        buckets = agg_result.get("buckets", [])
        if not buckets:
            lines.append("No buckets returned.")
        else:
            # Table header
            max_key_len = max(len(str(b.get("key", ""))) for b in buckets)
            col_width = max(max_key_len, 20)
            lines.append(f"  {'Value':<{col_width}}  Count")
            lines.append(f"  {'─' * col_width}  ─────")
            for bucket in buckets:
                key = str(bucket.get("key", ""))
                count = bucket.get("doc_count", 0)
                lines.append(f"  {key:<{col_width}}  {count}")
            # Show "other" count if present
            other = agg_result.get("sum_other_doc_count", 0)
            if other > 0:
                lines.append(f"  {'(other values)':<{col_width}}  {other}")
    elif agg_type == "cardinality":
        value = agg_result.get("value", 0)
        lines.append(f"Distinct values of {group_by}: {value}")
    elif agg_type in ("sum", "avg", "min", "max"):
        value = agg_result.get("value")
        lines.append(f"{agg_type}({group_by}): {value}")
    else:
        lines.append(f"Raw result: {json.dumps(agg_result, indent=2)}")

    output = "\n".join(lines)

    # Truncate if output is too large
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n\n... [OUTPUT TRUNCATED]"

    return output


def _format_fetch_response(
    response: Dict[str, Any],
    document_id: str,
    index: str,
) -> str:
    """Format a single document fetch response for LLM consumption."""
    responses = response.get("responses", [response])
    if not responses:
        return f"Document '{document_id}' not found in '{index}'."

    first_response = responses[0]

    if first_response.get("error"):
        error = first_response["error"]
        error_type = error.get("type", "unknown")
        error_reason = error.get("reason", str(error))
        return f"Fetch error ({error_type}): {error_reason}"

    hits = first_response.get("hits", {}).get("hits", [])
    if not hits:
        return f"Document '{document_id}' not found in '{index}'."

    hit = hits[0]
    source = hit.get("_source", {})
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except json.JSONDecodeError:
            return f"Error: Could not parse document data for '{document_id}'."

    lines = [
        f"Full document '{document_id}' from '{index}':",
        "─" * 50,
    ]
    _append_fields(lines, source, indent=2)

    output = "\n".join(lines)

    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n\n... [OUTPUT TRUNCATED — document too large]"

    return output


# ── Tool description builders ────────────────────────────────────────────────

def _build_search_tool_description(index: str, skill: Optional[str] = None) -> str:
    """Build the description for a search tool, optionally appending a skill."""
    base = (
        f"Search the '{index}' OpenSearch index using Lucene query syntax.\n"
        "Returns compact summaries of matching documents with key fields only.\n\n"
        "IMPORTANT:\n"
        "- For counting or statistics, prefer the aggregation tool instead.\n"
        "- For full document details, use fetch_monit_document with a document ID from the results.\n"
        "- Start with a narrow query and small result set; expand if needed.\n\n"
        "Input parameters:\n"
        "- query: Lucene query string (required).\n"
        "- from_time: Start time (default: 'now-24h'). Supports date math (e.g., now-7d, now-24h).\n"
        "- to_time: End time (default: 'now'). Supports date math.\n"
        f"- max_results: Max documents per page (default: {DEFAULT_MAX_RESULTS}, hard limit: {MAX_RESULTS_HARD_LIMIT}).\n"
        "- page: Page number for pagination (default: 1). Use page=2 to see the next batch of results.\n"
    )
    if skill:
        base += f"\n--- Domain Knowledge ---\n{skill}"
    return base


def _build_aggregation_tool_description(index: str, skill: Optional[str] = None) -> str:
    """Build the description for an aggregation tool, optionally appending a skill."""
    base = (
        f"Run aggregation queries on the '{index}' OpenSearch index.\n\n"
        "Use this for counting, grouping, statistics — NOT for fetching individual documents.\n"
        "PREFER this tool over the search tool when the question asks about counts, distributions,\n"
        "top errors, totals, averages, or any summary statistics.\n\n"
        "Input parameters:\n"
        "- query: Lucene query string to filter documents (required). Use '*' for all documents.\n"
        "- group_by: Field to aggregate on (required, e.g. 'data.reason').\n"
        "- agg_type: Aggregation type (default: 'terms'). One of: terms, sum, avg, min, max, cardinality.\n"
        "- top_n: Number of top buckets for terms aggregation (default: 10, max: 100).\n"
        "- from_time: Start time (default: 'now-24h'). Supports date math (e.g., now-7d, now-24h).\n"
        "- to_time: End time (default: 'now'). Supports date math.\n"
    )
    if skill:
        base += f"\n--- Domain Knowledge ---\n{skill}"
    return base


def _build_fetch_tool_description(index: str) -> str:
    """Build the description for the document fetch tool."""
    return (
        f"Fetch the full details of a single document from the '{index}' OpenSearch index by its ID.\n\n"
        "Use this tool AFTER searching to retrieve the complete contents of a specific document.\n"
        "The document_id comes from search results (shown as id=<value> in each result row).\n\n"
        "Input parameters:\n"
        "- document_id: The OpenSearch document _id (required).\n"
    )


# ── Tool factories ───────────────────────────────────────────────────────────

def create_monit_opensearch_search_tool(
    client: MONITOpenSearchClient,
    *,
    tool_name: str = "search_opensearch",
    index: str,
    skill: Optional[str] = None,
) -> Callable[..., str]:
    """
    Create a LangChain tool for searching an OpenSearch index via MONIT.

    Args:
        client: ``MONITOpenSearchClient`` instance.
        tool_name: Tool name for LangChain.
        index: Index pattern to query.
        skill: Optional skill markdown to append to the tool description.

    Returns:
        LangChain tool function.
    """
    tool_description = _build_search_tool_description(index, skill)
    summary_fields = _get_summary_fields(index)

    @tool(tool_name, description=tool_description)
    def _search_opensearch(
        query: str,
        from_time: str = "now-24h",
        to_time: str = "now",
        max_results: int = DEFAULT_MAX_RESULTS,
        page: int = 1,
    ) -> str:
        """
        Search OpenSearch for documents matching a Lucene query.

        Args:
            query: Lucene query string.
            from_time: Start time in date math (default: now-24h).
            to_time: End time in date math (default: now).
            max_results: Maximum number of results per page.
            page: Page number (default: 1).

        Returns:
            Formatted string with matching documents.
        """
        if not query or not query.strip():
            return "Please provide a non-empty Lucene query."

        effective_max = min(max_results, MAX_RESULTS_HARD_LIMIT)
        page = max(1, page)
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
                response, query.strip(), index, effective_max,
                from_time=from_time, to_time=to_time,
                summary_fields=summary_fields, page=page,
            )

        except requests.exceptions.Timeout:
            logger.warning("OpenSearch query timed out for query: %s", query)
            return (
                "Query timed out. The service may be slow or the query too broad. "
                "Try narrowing the time range or making the query more specific."
            )
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            logger.warning("OpenSearch HTTP error %s for query: %s", status_code, query)
            if status_code in (401, 403):
                return "Authentication failed. The token may be invalid or expired."
            return f"Query failed with HTTP error {status_code}. Please try again."
        except Exception as e:
            logger.error("OpenSearch query error: %s", e, exc_info=True)
            return f"Error querying OpenSearch: {e}"

    return _search_opensearch


def create_monit_fetch_document_tool(
    client: MONITOpenSearchClient,
    *,
    tool_name: str = "fetch_monit_document",
    index: str,
) -> Callable[..., str]:
    """
    Create a LangChain tool for fetching a single document by ID from MONIT.

    Args:
        client: ``MONITOpenSearchClient`` instance.
        tool_name: Tool name for LangChain.
        index: Index pattern to query.

    Returns:
        LangChain tool function.
    """
    tool_description = _build_fetch_tool_description(index)

    @tool(tool_name, description=tool_description)
    def _fetch_document(document_id: str) -> str:
        """
        Fetch a single OpenSearch document by its _id.

        Args:
            document_id: The document _id from search results.

        Returns:
            Formatted string with full document contents.
        """
        if not document_id or not document_id.strip():
            return "Please provide a document_id."

        try:
            response = client.get_document_by_id(
                document_id=document_id.strip(),
                index=index,
            )
            return _format_fetch_response(response, document_id.strip(), index)

        except requests.exceptions.Timeout:
            logger.warning("Fetch timed out for doc %s", document_id)
            return "Fetch timed out. Please try again."
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            logger.warning("Fetch HTTP error %s for doc %s", status_code, document_id)
            return f"Fetch failed with HTTP error {status_code}."
        except Exception as e:
            logger.error("Fetch error for doc %s: %s", document_id, e, exc_info=True)
            return f"Error fetching document: {e}"

    return _fetch_document


def create_monit_opensearch_aggregation_tool(
    client: MONITOpenSearchClient,
    *,
    tool_name: str = "aggregate_opensearch",
    index: str,
    skill: Optional[str] = None,
) -> Callable[..., str]:
    """
    Create a LangChain tool for running aggregation queries on an OpenSearch index.

    Args:
        client: ``MONITOpenSearchClient`` instance.
        tool_name: Tool name for LangChain.
        index: Index pattern to query.
        skill: Optional skill markdown to append to the tool description.

    Returns:
        LangChain tool function.
    """
    tool_description = _build_aggregation_tool_description(index, skill)

    @tool(tool_name, description=tool_description)
    def _aggregate_opensearch(
        query: str,
        group_by: str,
        agg_type: str = "terms",
        top_n: int = 10,
        from_time: str = "now-24h",
        to_time: str = "now",
    ) -> str:
        """
        Run an aggregation query on OpenSearch.

        Args:
            query: Lucene query string to filter documents.
            group_by: Field to aggregate on.
            agg_type: Aggregation type (terms, sum, avg, min, max, cardinality).
            top_n: Number of top buckets for terms aggregation.
            from_time: Start time in date math (default: now-24h).
            to_time: End time in date math (default: now).

        Returns:
            Formatted aggregation results.
        """
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

        except requests.exceptions.Timeout:
            logger.warning("OpenSearch aggregation timed out for query: %s", query)
            return (
                "Aggregation timed out. Try narrowing the time range or simplifying the query."
            )
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            logger.warning("OpenSearch HTTP error %s for aggregation: %s", status_code, query)
            if status_code in (401, 403):
                return "Authentication failed. The token may be invalid or expired."
            return f"Aggregation failed with HTTP error {status_code}. Please try again."
        except Exception as e:
            logger.error("OpenSearch aggregation error: %s", e, exc_info=True)
            return f"Error running aggregation: {e}"

    return _aggregate_opensearch


__all__ = [
    "MONITOpenSearchClient",
    "create_monit_opensearch_search_tool",
    "create_monit_opensearch_aggregation_tool",
    "create_monit_fetch_document_tool",
]
