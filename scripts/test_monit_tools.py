#!/usr/bin/env python3
"""Quick smoke test for the optimized MONIT tool output.

Requires MONIT_GRAFANA_TOKEN env var.  Runs against live MONIT API.

Usage:
    MONIT_GRAFANA_TOKEN=<token> python3 scripts/test_monit_tools.py
"""

import os
import sys

# Allow running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.archi.pipelines.agents.tools.monit_opensearch import (
    MONITOpenSearchClient,
    _format_opensearch_response,
    _format_fetch_response,
    _format_aggregation_response,
    _get_summary_fields,
    CONDOR_SUMMARY_FIELDS,
    RUCIO_SUMMARY_FIELDS,
    MAX_OUTPUT_CHARS,
)

TOKEN = os.environ.get("MONIT_GRAFANA_TOKEN")
if not TOKEN:
    print("ERROR: Set MONIT_GRAFANA_TOKEN env var")
    sys.exit(1)

CONDOR_URL = "https://monit-grafana.cern.ch/api/datasources/proxy/8787/_msearch"
RUCIO_URL = "https://monit-grafana.cern.ch/api/datasources/proxy/9269/_msearch"
CONDOR_INDEX = "monit_prod_condor_raw_metric*"
RUCIO_INDEX = "monit_prod_cms_rucio_raw_events*"


def test_field_projection():
    """Test that summary fields are correctly resolved for known indices."""
    assert _get_summary_fields(CONDOR_INDEX) == CONDOR_SUMMARY_FIELDS
    assert _get_summary_fields(RUCIO_INDEX) == RUCIO_SUMMARY_FIELDS
    assert _get_summary_fields("unknown_index") is None
    print("✓ Field projection lookup works")


def test_condor_search():
    """Test condor search with field projection and pagination."""
    client = MONITOpenSearchClient(url=CONDOR_URL, token=TOKEN)
    summary_fields = _get_summary_fields(CONDOR_INDEX)

    # Page 1
    response = client.search_with_lucene(
        lucene_query="data.Status:Completed",
        from_time="now-24h",
        to_time="now",
        size=3,
        offset=0,
        index=CONDOR_INDEX,
    )
    output = _format_opensearch_response(
        response, "data.Status:Completed", CONDOR_INDEX, 3,
        from_time="now-24h", to_time="now",
        summary_fields=summary_fields, page=1,
    )
    print(f"\n--- Condor Search (page 1, 3 results) ---")
    print(output)
    print(f"--- Output length: {len(output)} chars ---")
    assert len(output) < MAX_OUTPUT_CHARS, f"Output too large: {len(output)}"
    assert "page 1" in output
    assert "id=" in output
    assert "fetch_monit_document" in output
    print("✓ Condor search with projection works")

    # Page 2
    response2 = client.search_with_lucene(
        lucene_query="data.Status:Completed",
        from_time="now-24h",
        to_time="now",
        size=3,
        offset=3,
        index=CONDOR_INDEX,
    )
    output2 = _format_opensearch_response(
        response2, "data.Status:Completed", CONDOR_INDEX, 3,
        from_time="now-24h", to_time="now",
        summary_fields=summary_fields, page=2,
    )
    print(f"\n--- Condor Search (page 2) ---")
    print(output2)
    assert "page 2" in output2
    print("✓ Condor pagination works")


def test_rucio_search():
    """Test rucio search with field projection."""
    client = MONITOpenSearchClient(url=RUCIO_URL, token=TOKEN)
    summary_fields = _get_summary_fields(RUCIO_INDEX)

    response = client.search_with_lucene(
        lucene_query="data.event_type:transfer-failed",
        from_time="now-24h",
        to_time="now",
        size=3,
        offset=0,
        index=RUCIO_INDEX,
    )
    output = _format_opensearch_response(
        response, "data.event_type:transfer-failed", RUCIO_INDEX, 3,
        from_time="now-24h", to_time="now",
        summary_fields=summary_fields, page=1,
    )
    print(f"\n--- Rucio Search (3 results) ---")
    print(output)
    print(f"--- Output length: {len(output)} chars ---")
    assert len(output) < MAX_OUTPUT_CHARS
    assert "id=" in output
    print("✓ Rucio search with projection works")


def test_fetch_document():
    """Test fetching a single document by ID."""
    client = MONITOpenSearchClient(url=CONDOR_URL, token=TOKEN)

    # First get a doc ID from search
    response = client.search_with_lucene(
        lucene_query="data.Status:Completed",
        from_time="now-24h",
        to_time="now",
        size=1,
        offset=0,
        index=CONDOR_INDEX,
    )
    hits = response.get("responses", [response])[0].get("hits", {}).get("hits", [])
    if not hits:
        print("⚠ No condor documents found in last 24h, skipping fetch test")
        return

    doc_id = hits[0]["_id"]
    print(f"\nFetching document: {doc_id}")

    fetch_response = client.get_document_by_id(
        document_id=doc_id,
        index=CONDOR_INDEX,
    )
    output = _format_fetch_response(fetch_response, doc_id, CONDOR_INDEX)
    print(f"\n--- Fetch Document ---")
    print(output[:2000])
    if len(output) > 2000:
        print(f"... [{len(output)} total chars]")
    assert len(output) <= MAX_OUTPUT_CHARS + 100  # allow for truncation message
    assert doc_id in output
    print("✓ Fetch document works")


def test_aggregation():
    """Test aggregation still works."""
    client = MONITOpenSearchClient(url=CONDOR_URL, token=TOKEN)

    response = client.search_with_aggregation(
        lucene_query="data.Status:Completed",
        group_by="data.Site",
        agg_type="terms",
        top_n=5,
        from_time="now-24h",
        to_time="now",
        index=CONDOR_INDEX,
    )
    output = _format_aggregation_response(
        response, "data.Status:Completed", CONDOR_INDEX, "data.Site", "terms",
        from_time="now-24h", to_time="now",
    )
    print(f"\n--- Condor Aggregation ---")
    print(output)
    print(f"--- Output length: {len(output)} chars ---")
    assert len(output) < MAX_OUTPUT_CHARS
    print("✓ Aggregation works")


if __name__ == "__main__":
    test_field_projection()
    test_condor_search()
    test_rucio_search()
    test_fetch_document()
    test_aggregation()
    print("\n" + "=" * 60)
    print("All tests passed!")
