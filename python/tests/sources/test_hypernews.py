"""req.w2.sources-catalogs — HyperNewsSource emission from cache, offline."""
import hashlib
import json

from okg.substrate.library.sources.base import EdgeFact, NodeFact

from archi.sources.hypernews import HyperNewsSource

RECORDS = [
    {
        "thread_id": "comp-ops/123",
        "title": "Transfers stuck at T2_US_MIT",
        "url": "https://hypernews.cern.ch/HyperNews/CMS/get/comp-ops/123.html",
        "forum_name": "comp-ops",
        "body": "Transfers to T2_US_MIT are stuck since yesterday.",
        "author": "Ada Lovelace",
        "date": "2026-08-01",
        "reply_count": 3,
    },
]


def _source(tmp_path, **kwargs):
    root = tmp_path / "data" / "hypernews"
    root.mkdir(parents=True)
    (root / "records.json").write_text(json.dumps(RECORDS))
    return HyperNewsSource(base=str(tmp_path), **kwargs)


def test_thread_node_and_chunk_emission_from_cache(tmp_path):
    source = _source(tmp_path)
    run = source.run("run-1", mode="scope_complete")
    facts = list(run.facts)
    threads = [
        f for f in facts
        if isinstance(f, NodeFact) and f.subtype == "forum_thread"
    ]
    assert len(threads) == 1
    thread = threads[0]
    assert thread.node_id == "hn:comp-ops/123"
    assert thread.attrs["title"] == "Transfers stuck at T2_US_MIT"
    assert thread.attrs["forum_name"] == "comp-ops"
    assert thread.attrs["reply_count"] == 3
    chunks = [
        f for f in facts
        if isinstance(f, NodeFact) and f.subtype == "document_chunk"
    ]
    assert len(chunks) == 1
    chunk = chunks[0]
    text = chunk.attrs["text"]
    # Parity: the cms original hashes with a literal backslash-zero
    # separator (escaped f-string), like jira.py.
    expected = "chunk:" + hashlib.sha256(
        f"hn:comp-ops/123\\0{0}\\0{text}".encode("utf-8")
    ).hexdigest()[:16]
    assert chunk.node_id == expected
    assert chunk.attrs["chunker_name"] == "cms_hypernews_window_v1"
    assert chunk.attrs["heading_path"] == "Transfers stuck at T2_US_MIT"
    contains = [
        e for e in facts
        if isinstance(e, EdgeFact) and e.edge_type == "contains"
    ]
    assert [(e.src, e.dst) for e in contains] == [
        ("hn:comp-ops/123", chunk.node_id)
    ]
    assert run.health.mode == "cache"
    assert run.completed_scope is True


def test_reference_edges_with_configured_targets(tmp_path):
    (tmp_path / "data" / "cric").mkdir(parents=True)
    (tmp_path / "data" / "cric" / "sites.json").write_text(
        json.dumps({"T2_US_MIT": {}})
    )
    source = _source(tmp_path, sites_path="data/cric/sites.json")
    facts = list(source.run("run-1").facts)
    refs = [
        e for e in facts
        if isinstance(e, EdgeFact) and e.edge_type == "references"
    ]
    assert {(e.dst, e.attrs["match_type"]) for e in refs} == {
        ("site:T2_US_MIT", "cms_site"),
    }


def test_missing_cache_and_cookie_reports_not_ok_and_no_facts(tmp_path):
    source = HyperNewsSource(base=str(tmp_path), required=True)
    run = source.run("run-1", mode="reconcile")
    assert list(run.facts) == []
    assert run.completed_scope is False
    assert run.health.status != "ok"
