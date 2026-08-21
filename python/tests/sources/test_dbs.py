"""req.w2.sources-catalogs — DBSDatasetSource emission, offline."""
import json

from okg.substrate.library.sources.base import EdgeFact, NodeFact

from archi.sources.dbs import DBSDatasetSource

# DBS REST key spelling on purpose (dataset / *_ds_name aliases).
RECORDS = [
    {
        "dataset": "/TTto2L2Nu/Run3Summer23-v1/AODSIM",
        "data_tier_name": "AODSIM",
        "primary_ds_name": "TTto2L2Nu",
        "processed_ds_name": "Run3Summer23-v1",
        "physics_group_name": "TOP",
        "creation_date": "1700000000",
        "dataset_access_type": "VALID",
        "dataset_size": 123456789,
        "nfiles": 42,
        "nevents": 1000000,
    },
    {
        "dataset": "/TTto2L2Nu/Run3Summer23-v1/MINIAODSIM",
        "data_tier_name": "MINIAODSIM",
        "primary_ds_name": "TTto2L2Nu",
        "processed_ds_name": "Run3Summer23-v1",
    },
]


def _source(tmp_path, records):
    root = tmp_path / "data" / "dbs-datasets"
    root.mkdir(parents=True)
    (root / "records.json").write_text(json.dumps(records))
    return DBSDatasetSource(base=str(tmp_path))


def test_dataset_nodes_and_tier_chain_edges(tmp_path):
    source = _source(tmp_path, RECORDS)
    facts = list(source.run("run-1", mode="scope_complete").facts)
    nodes = {f.node_id: f for f in facts if isinstance(f, NodeFact)}
    assert set(nodes) == {
        "dataset:/TTto2L2Nu/Run3Summer23-v1/AODSIM",
        "dataset:/TTto2L2Nu/Run3Summer23-v1/MINIAODSIM",
    }
    aod = nodes["dataset:/TTto2L2Nu/Run3Summer23-v1/AODSIM"]
    assert aod.subtype == "dataset"
    assert aod.attrs["tier"] == "AODSIM"
    assert aod.attrs["era"] == "Run3Summer23"
    assert aod.attrs["primary_dataset"] == "TTto2L2Nu"
    assert aod.attrs["total_files"] == 42
    edges = [f for f in facts if isinstance(f, EdgeFact)]
    assert len(edges) == 1
    edge = edges[0]
    # MINIAODSIM (later tier) derives_from AODSIM
    assert edge.edge_type == "derives_from"
    assert edge.src == "dataset:/TTto2L2Nu/Run3Summer23-v1/MINIAODSIM"
    assert edge.dst == "dataset:/TTto2L2Nu/Run3Summer23-v1/AODSIM"
    assert edge.provenance == "derived_deterministic"
    assert edge.attrs["relationship"] == "tier_chain"


def test_preflight_optional_cache(tmp_path):
    missing = DBSDatasetSource(base=str(tmp_path))
    assert missing.preflight().status == "cache_missing"
    assert missing.preflight().required is False
    empty = _source(tmp_path, [])
    assert empty.preflight().status == "skipped_optional"
