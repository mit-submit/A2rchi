"""req.w2.sources-catalogs — CMSSWReleaseSource emission, offline.

Covers both modes: the cms cache-backed records path (canonical) and
the W1 releases.map option, which now flows through the same emission
(family nodes + supersedes edges).
"""
import json

from okg.substrate.library.sources.base import EdgeFact, NodeFact

from archi.sources.cmssw import CMSSWReleaseSource, parse_releases_map

RECORDS = [
    {
        "label": "CMSSW_14_0_1",
        "type": "Production",
        "state": "Announced",
        "architecture": ["el8_amd64_gcc12"],
        "release_date": "2024-03-01",
    },
    {
        "label": "CMSSW_14_0_2",
        "type": "Production",
        "state": "Announced",
        "architecture": ["el8_amd64_gcc12", "el9_amd64_gcc12"],
    },
    {
        "label": "CMSSW_14_0_2_patch1",
        "type": "Production",
        "state": "Announced",
        "architecture": "el8_amd64_gcc12",
    },
]

RELEASES_MAP = "\n".join([
    "architecture=el8_amd64_gcc12;label=CMSSW_14_0_1;type=Production;state=Announced;prodarch=1;",
    "architecture=el9_amd64_gcc12;label=CMSSW_14_0_1;type=Production;state=Announced;prodarch=0;",
    "architecture=el8_amd64_gcc12;label=CMSSW_14_0_2;type=Production;state=Announced;prodarch=1;",
    "architecture=el8_amd64_gcc12;label=NOT_A_RELEASE;type=Production;state=Announced;",
    "",
])


def _nodes(facts):
    return {f.node_id: f for f in facts if isinstance(f, NodeFact)}


def _supersedes(facts):
    return {
        (e.src, e.dst)
        for e in facts
        if isinstance(e, EdgeFact) and e.edge_type == "supersedes"
    }


def test_cache_backed_records_families_and_supersedes(tmp_path):
    root = tmp_path / "data" / "cmssw-releases"
    root.mkdir(parents=True)
    (root / "records.json").write_text(json.dumps(RECORDS))
    source = CMSSWReleaseSource(base=str(tmp_path))
    run = source.run("run-1", mode="scope_complete")
    facts = list(run.facts)
    nodes = _nodes(facts)
    assert set(nodes) == {
        "cmssw_release:CMSSW_14_0_X",
        "cmssw_release:CMSSW_14_0_1",
        "cmssw_release:CMSSW_14_0_2",
        "cmssw_release:CMSSW_14_0_2_patch1",
    }
    assert all(n.subtype == "cmssw_release" for n in nodes.values())
    family = nodes["cmssw_release:CMSSW_14_0_X"]
    assert family.attrs["release_type"] == "release_family"
    assert family.attrs["family"] is True
    rel = nodes["cmssw_release:CMSSW_14_0_2"]
    assert rel.attrs["major"] == 14 and rel.attrs["patch"] == 2
    assert rel.attrs["architecture"] == "el8_amd64_gcc12,el9_amd64_gcc12"
    patch = nodes["cmssw_release:CMSSW_14_0_2_patch1"]
    assert patch.attrs["release_type"] == "patch"
    assert _supersedes(facts) == {
        ("cmssw_release:CMSSW_14_0_2", "cmssw_release:CMSSW_14_0_1"),
        ("cmssw_release:CMSSW_14_0_2_patch1", "cmssw_release:CMSSW_14_0_2"),
    }
    assert run.completed_scope is True
    assert run.health.record_count == 3


def test_releases_map_mode_same_emission_shape(tmp_path):
    root = tmp_path / "data" / "cmssw-releases"
    root.mkdir(parents=True)
    (root / "releases.map").write_text(RELEASES_MAP)
    source = CMSSWReleaseSource(
        map_cache_path="data/cmssw-releases/releases.map",
        fetch=False,
        base=str(tmp_path),
    )
    facts = list(source.run("run-1", mode="scope_complete").facts)
    nodes = _nodes(facts)
    assert set(nodes) == {
        "cmssw_release:CMSSW_14_0_X",
        "cmssw_release:CMSSW_14_0_1",
        "cmssw_release:CMSSW_14_0_2",
    }
    # architectures aggregated across duplicate map lines, sorted
    rel1 = nodes["cmssw_release:CMSSW_14_0_1"]
    assert rel1.attrs["architecture"] == "el8_amd64_gcc12,el9_amd64_gcc12"
    assert _supersedes(facts) == {
        ("cmssw_release:CMSSW_14_0_2", "cmssw_release:CMSSW_14_0_1"),
    }


def test_parse_releases_map_limit():
    records = parse_releases_map(RELEASES_MAP, limit=1)
    assert [r.label for r in records] == ["CMSSW_14_0_2"]
