"""req.w2.sources-catalogs — SITECONFSource emission (fixture mode)."""
import json

from okg.substrate.library.sources.base import EdgeFact, NodeFact

from archi.sources.siteconf import SiteConfRecord, SITECONFSource

RECORD = SiteConfRecord(
    site_name="T2_US_MIT",
    storage_protocols=("XRootD", "WebDAV"),
    se_endpoints=("xrootd.cmsaf.mit.edu",),
    ce_endpoints=("ce01.cmsaf.mit.edu",),
    local_stage_out="T2_US_MIT",
    fallback_stage_out="T1_US_FNAL",
    frontier_config="http://frontier.cern.ch",
    raw_storage_json='[{"protocol": "XRootD"}]',
)


def _source(tmp_path, records):
    (tmp_path / "data" / "cric").mkdir(parents=True)
    (tmp_path / "data" / "cric" / "sites.json").write_text(
        json.dumps({"T2_US_MIT": {}})
    )
    return SITECONFSource(records=records, base=str(tmp_path))


def test_fixture_records_emit_site_config_and_contains_edge(tmp_path):
    source = _source(tmp_path, [RECORD])
    run = source.run("run-1", mode="scope_complete")
    facts = list(run.facts)
    nodes = [f for f in facts if isinstance(f, NodeFact)]
    assert len(nodes) == 1
    node = nodes[0]
    assert node.node_id == "siteconf:T2_US_MIT"
    assert node.subtype == "site_config"
    assert node.attrs["storage_protocols"] == ["XRootD", "WebDAV"]
    assert node.attrs["se_endpoints"] == ["xrootd.cmsaf.mit.edu"]
    assert node.attrs["local_stage_out"] == "T2_US_MIT"
    assert "SITECONF for T2_US_MIT" in node.attrs["text"]
    edges = [f for f in facts if isinstance(f, EdgeFact)]
    assert [(e.src, e.edge_type, e.dst) for e in edges] == [
        ("site:T2_US_MIT", "contains", "siteconf:T2_US_MIT"),
    ]
    assert edges[0].provenance == "authoritative"
    assert run.health.mode == "fixture"


def test_unknown_site_gets_node_but_no_edge_and_preflight_fixture(tmp_path):
    record = SiteConfRecord(site_name="T2_XX_Nowhere", raw_storage_json="{}")
    source = _source(tmp_path, [record])
    facts = list(source.run("run-1").facts)
    assert [f.node_id for f in facts if isinstance(f, NodeFact)] == [
        "siteconf:T2_XX_Nowhere"
    ]
    assert [f for f in facts if isinstance(f, EdgeFact)] == []
    result = source.preflight()
    assert result.status == "ok"
    assert result.mode == "fixture"
    assert result.record_count == 1
