"""task.w2.sources-monit — the four MONIT sources, offline.

Fixtures mirror the two cache shapes the cms originals consume: a JSON
list of plain record mappings, or a raw MONIT ``_msearch`` response
(``{"responses": [...]}``). Live paths run against a monkeypatched
``requests.post``.
"""
import json

import pytest

from okg.substrate.library.sources.base import (
    EdgeFact,
    NodeFact,
    ProgressMarker,
)
from okg.substrate.library.sources.mutable_api_probe import MutableApiProbe

import archi.sources.monit as monit_mod
from archi.sources.monit import (
    MONITCondorSource,
    MONITRucioDatasetSource,
    MONITRucioTransferSource,
    MONITSAMSource,
    _today,
)

TOKEN_ENV = "ARCHI_T_MONIT_TOKEN"


def _nodes(facts, subtype):
    return [f for f in facts if isinstance(f, NodeFact) and f.subtype == subtype]


def _edges(facts, edge_type):
    return [f for f in facts if isinstance(f, EdgeFact) and f.edge_type == edge_type]


class _FakeHTTPResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _fake_post(monkeypatch, respond):
    """Patch requests.post; `respond(url, headers, data)` -> payload."""
    calls = []

    def _post(url, headers=None, data=None, timeout=None):
        calls.append({"url": url, "headers": headers, "data": data})
        payload = respond(url, headers, data)
        return _FakeHTTPResponse(payload)

    monkeypatch.setattr(monit_mod.requests, "post", _post)
    return calls


# --- fixtures: raw MONIT _msearch response shapes -----------------------------

SITEMON_RESPONSE = {
    "responses": [{
        "aggregations": {"groups": {"buckets": [
            {
                "key": "T1_DE_KIT",
                "status_breakdown": {"buckets": [
                    {"key": "OK", "doc_count": 18},
                    {"key": "WARNING", "doc_count": 1},
                    {"key": "CRITICAL", "doc_count": 1},
                ]},
            },
            {
                "key": "T3_US_Baylor",
                "status_breakdown": {"buckets": [
                    {"key": "OK", "doc_count": 1},
                    {"key": "CRITICAL", "doc_count": 3},
                ]},
            },
            {"key": "", "status_breakdown": {"buckets": []}},
        ]}}
    }]
}

CONDOR_RESPONSE = {
    "responses": [{
        "aggregations": {"sites": {"buckets": [{
            "key": "T2_US_MIT",
            "doc_count": 100,
            "total_jobs": {"value": 120},
            "total_core_hrs": {"value": 400.0},
            "total_cpu_time_hrs": {"value": 300.0},
            "avg_queue_hrs": {"value": 2.5},
            "by_status": {"buckets": [
                {"key": "Running", "doc_count": 60},
                {"key": "Idle", "doc_count": 30},
                {"key": "Held", "doc_count": 10},
            ]},
            "by_job_type": {"buckets": [
                {"key": "analysis", "doc_count": 80},
            ]},
            "by_error": {"buckets": [
                {"key": "SystemError", "doc_count": 4},
            ]},
        }]}}
    }]
}

TRANSFER_RESPONSE = {
    "responses": [{
        "aggregations": {"by_src_rse": {"buckets": [
            {
                "key": "T1_DE_KIT_Disk",
                "by_dst_rse": {"buckets": [{
                    "key": "T2_US_MIT",
                    "bytes_total": {"value": 1000},
                    "avg_duration": {"value": 100.0},
                    "by_event_type": {"buckets": [
                        {"key": "transfer-done", "doc_count": 8},
                        {"key": "transfer-failed", "doc_count": 2},
                    ]},
                    "top_activity": {"buckets": [
                        {"key": "Production Output", "doc_count": 9},
                    ]},
                    "failure_reasons": {"buckets": [
                        {"key": "CHECKSUM MISMATCH", "doc_count": 2},
                    ]},
                }]},
            },
            {
                # Same site pair through the _Tape RSE: must merge.
                "key": "T1_DE_KIT_Tape",
                "by_dst_rse": {"buckets": [{
                    "key": "T2_US_MIT_Disk",
                    "bytes_total": {"value": 500},
                    "avg_duration": {"value": 200.0},
                    "by_event_type": {"buckets": [
                        {"key": "transfer-done", "doc_count": 5},
                    ]},
                    "top_activity": {"buckets": []},
                    "failure_reasons": {"buckets": [
                        {"key": "CHECKSUM MISMATCH", "doc_count": 1},
                    ]},
                }]},
            },
        ]}}
    }]
}

DATASET_BUCKET = {
    "key": {"dataset": "/Prim/Era-v1/RECO"},
    "replicas": {"buckets": [
        {"key": "T1_DE_KIT_Disk"},
        {"key": "T2_US_MIT"},
    ]},
    "sample": {"hits": {"hits": [{
        "_source": {"data": {
            "data_tier_name": "RECO",
            "acquisition_era_name": "Era",
            "dbs_event_count": 5,
            "dbs_n_files": 2,
            "dbs_size": 100,
            "physics_group_name": "Tracker",
        }}
    }]}},
}

DATASET_RESPONSE = {
    "responses": [{
        "aggregations": {"datasets": {"buckets": [
            DATASET_BUCKET,
            {
                "key": {"dataset": "/GenericTTbar/x/GEN-SIM"},
                "replicas": {"buckets": [{"key": "T2_CH_CERN"}]},
                "sample": {"hits": {"hits": []}},
            },
        ]}}
    }]
}


def _sam_source(tmp_path, **overrides):
    kwargs = {
        "records_path": "data/monit-sam/records.json",
        "token_env": TOKEN_ENV,
        "base": str(tmp_path),
    }
    kwargs.update(overrides)
    return MONITSAMSource(**kwargs)


def _write(tmp_path, rel, payload):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


# --- SAM ---------------------------------------------------------------------

def test_sam_cache_raw_response_parse_and_emission(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _write(tmp_path, "data/monit-sam/records.json", SITEMON_RESPONSE)
    source = _sam_source(tmp_path)
    run = source.run("run-1", mode="scope_complete")
    facts = list(run.facts)
    snaps = {n.node_id: n for n in _nodes(facts, "monitoring_snapshot")}
    today = _today()
    assert set(snaps) == {
        f"monitoring_snapshot:sam:T1_DE_KIT:{today}",
        f"monitoring_snapshot:sam:T3_US_Baylor:{today}",
    }
    kit = snaps[f"monitoring_snapshot:sam:T1_DE_KIT:{today}"]
    assert kit.attrs["value"] == 90.0
    assert kit.attrs["status"] == "OK"  # >= 90 -> OK
    assert kit.attrs["total_tests"] == 20
    assert kit.attrs["ok_count"] == 18
    assert kit.attrs["metric"] == "sam_site_availability"
    assert kit.source_revision["run_id"] == "run-1"
    assert kit.source_revision["mode"] == "cache"
    baylor = snaps[f"monitoring_snapshot:sam:T3_US_Baylor:{today}"]
    assert baylor.attrs["status"] == "CRITICAL"  # 25% < 70
    hosts = _edges(facts, "hosts")
    assert {(e.src, e.dst) for e in hosts} == {
        ("site:T1_DE_KIT", kit.node_id),
        ("site:T3_US_Baylor", baylor.node_id),
    }
    assert run.completed_scope is True
    assert run.health.status == "ok"
    assert run.health.mode == "cache"
    assert run.health.record_count == 2
    # Cache runs never carry credential refs or a live endpoint.
    assert run.health.credential_refs == ()
    assert run.health.endpoint is None


def test_sam_cache_list_of_records(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _write(tmp_path, "data/monit-sam/records.json", [{
        "site": "T2_US_MIT",
        "snapshot_date": "2026-08-01",
        "availability_pct": 75.0,
        "status": "WARNING",
        "total_tests": 4,
        "ok_count": 3,
    }])
    facts = list(_sam_source(tmp_path).run("r").facts)
    snap = _nodes(facts, "monitoring_snapshot")[0]
    assert snap.node_id == "monitoring_snapshot:sam:T2_US_MIT:2026-08-01"
    assert snap.attrs["value"] == 75.0
    assert snap.attrs["observed_at"] == "2026-08-01"


def test_sam_completed_scope_by_mode(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _write(tmp_path, "data/monit-sam/records.json", SITEMON_RESPONSE)
    source = _sam_source(tmp_path)
    assert source.run("r", mode="cursor").completed_scope is False
    assert source.run("r", mode="scope_complete").completed_scope is True
    assert source.run("r", mode="reconcile").completed_scope is True


def test_sam_missing_credential_never_completes_scope(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    source = _sam_source(tmp_path)  # no cache file, no token
    run = source.run("r", mode="scope_complete")
    assert list(run.facts) == []
    assert run.completed_scope is False
    assert run.health.status == "missing_credential"
    assert run.health.credential_refs == (TOKEN_ENV,)
    assert TOKEN_ENV in run.health.reason


def test_sam_corrupt_cache_is_endpoint_failed_not_complete(
    tmp_path, monkeypatch
):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    path = tmp_path / "data" / "monit-sam" / "records.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    run = _sam_source(tmp_path).run("r", mode="scope_complete")
    assert list(run.facts) == []
    assert run.completed_scope is False
    assert run.health.status == "endpoint_failed"


def test_sam_empty_result_is_skipped_optional(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _write(tmp_path, "data/monit-sam/records.json", [])
    run = _sam_source(tmp_path).run("r")
    assert list(run.facts) == []
    assert run.health.status == "skipped_optional"
    assert "no site buckets" in run.health.reason


def test_sam_preflight_states(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    source = _sam_source(tmp_path)
    result = source.preflight()
    assert result.status == "missing_credential"
    assert result.credential_refs == (TOKEN_ENV,)
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    result = source.preflight()
    assert result.status == "ok"
    assert result.mode == "live"
    assert result.endpoint == "https://monit-grafana.cern.ch"
    _write(tmp_path, "data/monit-sam/records.json", SITEMON_RESPONSE)
    result = source.preflight()
    assert result.status == "ok"
    assert result.mode == "cache"
    assert result.record_count == 2
    assert result.content_hash


def test_sam_live_query_and_auth_header(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    calls = _fake_post(monkeypatch, lambda *a: SITEMON_RESPONSE)
    source = _sam_source(
        tmp_path,
        grafana_base_url="https://grafana.example.org/",
        datasource_id=4242,
        index="sitemon-test-*",
        query_filter="data.vo:other",
        max_sites=7,
    )
    run = source.run("r", mode="scope_complete")
    facts = list(run.facts)
    assert len(_nodes(facts, "monitoring_snapshot")) == 2
    assert run.health.status == "ok"
    assert run.health.mode == "live"
    assert run.health.credential_refs == (TOKEN_ENV,)
    assert run.health.endpoint == "https://grafana.example.org"
    (call,) = calls
    assert call["url"] == (
        "https://grafana.example.org/api/datasources/proxy/4242/_msearch"
    )
    assert call["headers"]["Authorization"] == "Bearer test-token"
    meta_line, query_line, _ = call["data"].split("\n")
    assert json.loads(meta_line)["index"] == ["sitemon-test-*"]
    query = json.loads(query_line)
    assert query["aggs"]["groups"]["terms"]["size"] == 7
    assert (
        query["query"]["bool"]["must"][0]["query_string"]["query"]
        == "data.vo:other"
    )


def test_sam_live_opensearch_error_is_endpoint_failed(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    _fake_post(monkeypatch, lambda *a: {
        "responses": [{"error": {"type": "x_content_parse_exception",
                                 "reason": "bad query"}}]
    })
    run = _sam_source(tmp_path).run("r", mode="scope_complete")
    assert list(run.facts) == []
    assert run.completed_scope is False
    assert run.health.status == "endpoint_failed"
    assert "bad query" in run.health.reason


def test_monit_probe_declarations(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    source = _sam_source(tmp_path)
    for cls in (MONITSAMSource, MONITCondorSource,
                MONITRucioTransferSource, MONITRucioDatasetSource):
        assert cls.profile == "mutable_api"
        assert cls.change_probe_kind == "mutable_api"
    assert isinstance(source.change_probe, MutableApiProbe)
    # No cache: each probe token is fresh (forced live read).
    assert source.change_probe.build_token() != source.change_probe.build_token()
    # Cache present: token is stable until the cache bytes change.
    _write(tmp_path, "data/monit-sam/records.json", SITEMON_RESPONSE)
    source = _sam_source(tmp_path)
    first = source.change_probe.build_token()
    assert source.change_probe.build_token() == first
    _write(tmp_path, "data/monit-sam/records.json", [])
    assert source.change_probe.build_token() != first


def test_source_name_param(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    source = _sam_source(tmp_path, source_name="monit_sam_dev")
    assert source.preflight().source_name == "monit_sam_dev"


# --- Condor ------------------------------------------------------------------

def test_condor_cache_raw_response(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _write(tmp_path, "data/monit-condor/records.json", CONDOR_RESPONSE)
    source = MONITCondorSource(
        records_path="data/monit-condor/records.json",
        token_env=TOKEN_ENV,
        base=str(tmp_path),
    )
    run = source.run("r", mode="scope_complete")
    facts = list(run.facts)
    snap = _nodes(facts, "monitoring_snapshot")[0]
    today = _today()
    assert snap.node_id == f"monitoring_snapshot:condor:T2_US_MIT:{today}"
    assert snap.attrs["metric"] == "condor_compute_summary"
    assert snap.attrs["total_jobs"] == 120
    assert snap.attrs["core_hours"] == 400.0
    assert snap.attrs["cpu_efficiency"] == 0.75
    assert snap.attrs["avg_queue_hours"] == 2.5
    assert snap.attrs["jobs_running"] == 60
    assert snap.attrs["jobs_idle"] == 30
    assert snap.attrs["jobs_held"] == 10
    assert snap.attrs["job_type_breakdown"] == {"analysis": 80}
    assert snap.attrs["error_breakdown"] == {"SystemError": 4}
    (edge,) = _edges(facts, "hosts")
    assert edge.src == "site:T2_US_MIT"
    assert edge.dst == snap.node_id
    assert run.health.status == "ok"


# --- Rucio transfer ------------------------------------------------------------

def test_rucio_transfer_rse_merge_and_edges(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _write(
        tmp_path, "data/monit-rucio-transfer/records.json", TRANSFER_RESPONSE
    )
    source = MONITRucioTransferSource(
        records_path="data/monit-rucio-transfer/records.json",
        token_env=TOKEN_ENV,
        base=str(tmp_path),
    )
    facts = list(source.run("r", mode="scope_complete").facts)
    (node,) = _nodes(facts, "transfer_job")
    today = _today()
    # _Disk and _Tape RSEs collapse onto one site pair and merge.
    assert node.node_id == f"transfer_job:rucio:T1_DE_KIT:T2_US_MIT:{today}"
    assert node.attrs["done_count"] == 13
    assert node.attrs["failed_count"] == 2
    assert node.attrs["total_bytes"] == 1500
    assert node.attrs["num_files"] == 15
    assert node.attrs["state"] == "mixed"
    assert node.attrs["success_rate"] == round(13 / 15, 4)
    # Weighted average: (100*10 + 200*5) / 15
    assert node.attrs["avg_duration_s"] == round(2000 / 15, 2)
    assert node.attrs["failure_reasons"] == {"CHECKSUM MISMATCH": 3}
    assert node.attrs["top_activity"] == "Production Output"
    hosts = _edges(facts, "hosts")
    assert {(e.src, e.attrs["role"]) for e in hosts} == {
        ("site:T1_DE_KIT", "transfer_source"),
        ("site:T2_US_MIT", "transfer_destination"),
    }
    (t_edge,) = _edges(facts, "transfers_to")
    assert (t_edge.src, t_edge.dst) == ("site:T1_DE_KIT", "site:T2_US_MIT")


def test_rucio_transfer_list_cache_dedupes(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _write(tmp_path, "data/monit-rucio-transfer/records.json", [
        {"src_rse": "T1_DE_KIT_Disk", "dst_rse": "T2_US_MIT",
         "snapshot_date": "2026-08-01", "done_count": 3, "bytes_total": 10},
        {"src_rse": "T1_DE_KIT_Tape", "dst_rse": "T2_US_MIT",
         "snapshot_date": "2026-08-01", "failed_count": 1, "bytes_total": 5},
    ])
    source = MONITRucioTransferSource(
        records_path="data/monit-rucio-transfer/records.json",
        token_env=TOKEN_ENV,
        base=str(tmp_path),
    )
    facts = list(source.run("r").facts)
    (node,) = _nodes(facts, "transfer_job")
    assert node.attrs["done_count"] == 3
    assert node.attrs["failed_count"] == 1
    assert node.attrs["total_bytes"] == 15


# --- Rucio datasets -------------------------------------------------------------

def _dataset_source(tmp_path, **overrides):
    kwargs = {
        "records_path": "data/monit-rucio-datasets/records.json",
        "page_cache_dir": "data/monit-rucio-datasets/pages",
        "token_env": TOKEN_ENV,
        "base": str(tmp_path),
    }
    kwargs.update(overrides)
    return MONITRucioDatasetSource(**kwargs)


def test_rucio_dataset_cache_raw_response(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _write(
        tmp_path, "data/monit-rucio-datasets/records.json", DATASET_RESPONSE
    )
    run = _dataset_source(tmp_path).run("r", mode="scope_complete")
    facts = list(run.facts)
    # The GenericTTbar functional-test dataset is excluded.
    (node,) = _nodes(facts, "dataset")
    assert node.node_id == "dataset:/Prim/Era-v1/RECO"
    assert node.attrs["tier"] == "RECO"
    assert node.attrs["era"] == "Era"
    assert node.attrs["event_count"] == 5
    assert node.attrs["file_count"] == 2
    assert node.attrs["total_size_bytes"] == 100
    assert node.attrs["physics_group"] == "Tracker"
    assert node.attrs["replica_sites"] == ["T1_DE_KIT", "T2_US_MIT"]
    assert node.attrs["replica_rses"] == ["T1_DE_KIT_Disk", "T2_US_MIT"]
    assert node.attrs["overlay_source"] == "monit_rucio_daily_stats"
    hosts = _edges(facts, "hosts")
    assert {(e.src, e.attrs["relationship"]) for e in hosts} == {
        ("site:T1_DE_KIT", "dataset_replica"),
        ("site:T2_US_MIT", "dataset_replica"),
        ("se:T1_DE_KIT_Disk", "dataset_replica_rse"),
        ("se:T2_US_MIT", "dataset_replica_rse"),
    }
    assert run.completed_scope is True


def test_rucio_dataset_exclude_patterns_param(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _write(
        tmp_path, "data/monit-rucio-datasets/records.json", DATASET_RESPONSE
    )
    source = _dataset_source(tmp_path, exclude_dataset_patterns=("/Prim/",))
    facts = list(source.run("r").facts)
    (node,) = _nodes(facts, "dataset")
    assert node.node_id == "dataset:/GenericTTbar/x/GEN-SIM"


def test_rucio_dataset_missing_credential(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    run = _dataset_source(tmp_path).run("r", mode="scope_complete")
    assert list(run.facts) == []
    assert run.completed_scope is False
    assert run.health.status == "missing_credential"


def test_rucio_dataset_live_pagination_progress_and_page_cache(
    tmp_path, monkeypatch
):
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    page2_bucket = {
        "key": {"dataset": "/Second/Era-v1/AOD"},
        "replicas": {"buckets": [{"key": "T2_CH_CERN"}]},
        "sample": {"hits": {"hits": []}},
    }
    page1 = {"responses": [{"aggregations": {"datasets": {
        "buckets": [DATASET_BUCKET],
        "after_key": {"dataset": "/Prim/Era-v1/RECO"},
    }}}]}
    page2 = {"responses": [{"aggregations": {"datasets": {
        "buckets": [page2_bucket],
    }}}]}

    def _respond(url, headers, data):
        query = json.loads(data.split("\n")[1])
        composite = query["aggs"]["datasets"]["composite"]
        return page2 if "after" in composite else page1

    calls = _fake_post(monkeypatch, _respond)
    source = _dataset_source(tmp_path)  # no records cache -> live stream
    run = source.run("r", mode="scope_complete")
    facts = list(run.facts)
    assert run.completed_scope is True
    assert run.health.status == "ok"
    assert run.health.mode == "live"
    nodes = _nodes(facts, "dataset")
    assert {n.node_id for n in nodes} == {
        "dataset:/Prim/Era-v1/RECO",
        "dataset:/Second/Era-v1/AOD",
    }
    markers = [f for f in facts if isinstance(f, ProgressMarker)]
    assert {m.record_id for m in markers} == {
        "/Prim/Era-v1/RECO",
        "/Second/Era-v1/AOD",
    }
    assert len(calls) == 2
    # Live pages were cached under the query fingerprint...
    pages = sorted(
        (tmp_path / "data" / "monit-rucio-datasets" / "pages").rglob("*.json")
    )
    assert [p.name for p in pages] == ["page-000000.json", "page-000001.json"]
    # ...and a rerun with matching progress skips re-emission but still
    # yields the per-record markers (and reads from the page cache).
    progress = {f"record:{m.record_id}": m.fingerprint for m in markers}
    rerun = source.run("r2", mode="scope_complete", since_progress=progress)
    rerun_facts = list(rerun.facts)
    assert _nodes(rerun_facts, "dataset") == []
    assert len(
        [f for f in rerun_facts if isinstance(f, ProgressMarker)]
    ) == 2
    assert len(calls) == 2  # no new HTTP reads


def test_rucio_dataset_list_cache_records(tmp_path, monkeypatch):
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    _write(tmp_path, "data/monit-rucio-datasets/records.json", [{
        "dataset": "/Prim/Era-v1/RECO",
        "snapshot_date": "2026-08-01",
        "data_tier": "RECO",
        "replica_sites": ["T1_DE_KIT_Disk"],
        "replica_rses": ["T1_DE_KIT_Disk"],
    }])
    facts = list(_dataset_source(tmp_path).run("r").facts)
    (node,) = _nodes(facts, "dataset")
    assert node.attrs["replica_sites"] == ["T1_DE_KIT"]
    assert node.attrs["replica_rses"] == ["T1_DE_KIT_Disk"]


def test_required_kwonly_and_no_positional_params():
    with pytest.raises(TypeError):
        MONITSAMSource("positional")  # keyword-only constructor
