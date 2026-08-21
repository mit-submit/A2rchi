"""req.w2.sources-catalogs — DQMSource emission, offline."""
import json

from okg.substrate.library.sources.base import EdgeFact, NodeFact

from archi.sources.dqm import DQMSource

RECORDS = [
    {
        "filename": "Cert_Collisions2024_378981_385194_Golden.json",
        "cert_name": "Cert_Collisions2024_378981_385194_Golden",
        "run_range": [378981, 385194],
        "num_lumi_sections": 12345,
        "datasets": ["/Muon0/Run2024C-PromptReco-v1/DQMIO"],
    },
]


def _source(tmp_path):
    root = tmp_path / "data" / "dqm"
    root.mkdir(parents=True)
    (root / "records.json").write_text(json.dumps(RECORDS))
    return DQMSource(base=str(tmp_path))


def test_certification_run_and_dataset_emission(tmp_path):
    source = _source(tmp_path)
    facts = list(source.run("run-1", mode="scope_complete").facts)
    nodes = {f.node_id: f for f in facts if isinstance(f, NodeFact)}
    cert_id = "data_certification:Cert_Collisions2024_378981_385194_Golden"
    assert set(nodes) == {
        cert_id,
        "run:378981",
        "run:385194",
        "dataset:/Muon0/Run2024C-PromptReco-v1/DQMIO",
    }
    cert = nodes[cert_id]
    assert cert.subtype == "data_certification"
    assert cert.attrs["certification_type"] == "golden"
    assert cert.attrs["dataset_group"] == "Collisions2024"
    assert cert.attrs["year"] == 2024
    assert cert.attrs["run_min"] == 378981
    assert cert.attrs["run_max"] == 385194
    assert nodes["run:378981"].subtype == "run"
    assert nodes["run:378981"].attrs["run_number"] == 378981
    edges = {
        (e.src, e.edge_type, e.dst)
        for e in facts
        if isinstance(e, EdgeFact)
    }
    assert edges == {
        (cert_id, "recorded_during", "run:378981"),
        (cert_id, "recorded_during", "run:385194"),
        (
            cert_id,
            "references",
            "dataset:/Muon0/Run2024C-PromptReco-v1/DQMIO",
        ),
    }


def test_preflight_ok_with_hash(tmp_path):
    source = _source(tmp_path)
    result = source.preflight()
    assert result.status == "ok"
    assert result.record_count == 1
    assert result.content_hash
