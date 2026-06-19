#!/usr/bin/env python3
"""Preflight guard for corrected ORCD/vLLM benchmark runs."""

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional


REPO = Path(os.environ.get("ORCD_REPO", os.getcwd()))
DEFAULT_CONTRACT = REPO / "configs/submit75/orcd_vllm_corrected_contract.json"
SECRETS_DIR = Path(os.environ.get("ARCHI_SECRETS_DIR", os.path.expanduser("~/.archi-bundle-state/bundle/secrets/archi")))
OKG_MCP_READ_TOOLS = [
    "inspect",
    "search",
    "expand",
    "filter",
    "map",
    "aggregate",
    "query",
]
OKG_MCP_LIVE_TOOLS = [
    "cms_monit_rucio_search",
    "cms_monit_rucio_aggregate",
    "cms_monit_condor_search",
    "cms_monit_condor_aggregate",
]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path):
    return json.loads(path.read_text())


def strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            return text[end + 3:].lstrip()
    return text


def fail(failures, message: str) -> None:
    failures.append(message)


def http_json(url: str, params: Optional[dict] = None, timeout: float = 10.0):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"Connection": "close"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body.decode("utf-8"))


def pg_password() -> Optional[str]:
    if os.environ.get("PG_PASSWORD"):
        return os.environ["PG_PASSWORD"]
    f = SECRETS_DIR / "pg_password.txt"
    if f.exists():
        return f.read_text().strip()
    return None


def pg_config_from_env():
    url = os.environ.get("ARCHI_POSTGRES_URL")
    if url:
        parsed = urllib.parse.urlparse(url)
        return {
            "host": parsed.hostname or "127.0.0.1",
            "port": parsed.port or 5436,
            "user": parsed.username or "archi",
            "dbname": parsed.path.lstrip("/") or "archi-db",
            "password": pg_password(),
            "connect_timeout": 10,
        }
    if os.environ.get("PGHOST"):
        return {
            "host": os.environ.get("PGHOST", "127.0.0.1"),
            "port": int(os.environ.get("PGPORT", "5436")),
            "user": os.environ.get("PGUSER", "archi"),
            "dbname": os.environ.get("PGDATABASE", "archi-db"),
            "password": pg_password(),
            "connect_timeout": 10,
        }
    return None


PG_COUNT_SQL = """
select 'documents', count(*)::text
  from documents
 where not coalesce(is_deleted,false)
union all
select 'document_chunks', count(*)::text
  from document_chunks
union all
select 'vectorized_hashes', count(distinct d.resource_hash)::text
  from document_chunks c
  join documents d on d.id=c.document_id
 where c.embedding is not null
   and not coalesce(d.is_deleted,false)
union all
select 'documents_without_chunks', count(*)::text
  from documents d
  left join document_chunks c on c.document_id=d.id
 where c.id is null
   and not coalesce(d.is_deleted,false)
union all
select 'orphan_chunks', count(*)::text
  from document_chunks c
  left join documents d on d.id=c.document_id
 where d.id is null
union all
select 'pg_activity_' || coalesce(state, 'null'), count(*)::text
  from pg_stat_activity
 group by 1
"""


def _pg_counts_with_psycopg2(cfg: dict) -> dict:
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(**cfg)
    try:
        cur = conn.cursor()
        out = {}
        cur.execute(PG_COUNT_SQL)
        pg_activity = {}
        for key, value in cur.fetchall():
            if str(key).startswith("pg_activity_"):
                pg_activity[str(key)[len("pg_activity_"):]] = int(value)
            else:
                out[str(key)] = int(value)
        out["pg_activity"] = pg_activity
        return out
    finally:
        conn.close()


def _pg_counts_with_psql(cfg: dict) -> dict:
    cmd_prefix = None
    psql = shutil.which("psql")
    if psql:
        cmd_prefix = [psql]
    else:
        apptainer = shutil.which("apptainer")
        sif = Path(os.environ.get("ARCHI_POSTGRES_SIF", os.path.expanduser("~/.archi-bundle-state/sif/archi-postgres.sif")))
        if apptainer and sif.exists():
            cmd_prefix = [apptainer, "exec", str(sif), "psql"]
    if not cmd_prefix:
        raise RuntimeError("psql unavailable and no apptainer postgres image found")

    env = os.environ.copy()
    if cfg.get("password"):
        env["PGPASSWORD"] = str(cfg["password"])
    cmd = cmd_prefix + [
        "-h", str(cfg["host"]),
        "-p", str(cfg["port"]),
        "-U", str(cfg["user"]),
        "-d", str(cfg["dbname"]),
        "-At",
        "-F", "\t",
        "-c", PG_COUNT_SQL,
    ]
    proc = subprocess.run(cmd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=20)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "psql counts failed")
    out = {}
    pg_activity = {}
    for raw in proc.stdout.splitlines():
        if not raw.strip():
            continue
        key, value = raw.split("\t", 1)
        if key.startswith("pg_activity_"):
            pg_activity[key[len("pg_activity_"):]] = int(value)
        else:
            out[key] = int(value)
    out["pg_activity"] = pg_activity
    return out


def pg_counts():
    cfg = pg_config_from_env()
    if not cfg:
        return {"skipped": "no postgres env"}
    try:
        return _pg_counts_with_psycopg2(cfg)
    except ModuleNotFoundError:
        return _pg_counts_with_psql(cfg)


def corpus_snapshot():
    root = Path(os.environ.get("ARCHI_CORPUS_DIR", os.path.expanduser("~/.archi-bundle-state/corpus")))
    if not root.exists():
        return {"skipped": f"{root} missing"}
    rels = sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())
    digest = hashlib.sha256(("\n".join(rels) + ("\n" if rels else "")).encode("utf-8")).hexdigest()
    return {"file_count": len(rels), "filelist_sha256": digest, "root": str(root)}


def vectorstore_probe():
    from langchain_huggingface import HuggingFaceEmbeddings
    from src.data_manager.vectorstore.postgres_vectorstore import PostgresVectorStore

    cfg = pg_config_from_env()
    if not cfg:
        return {"skipped": "no postgres env"}
    collection = os.environ.get("ARCHI_VECTOR_COLLECTION", "default_collection_with_HuggingFaceEmbeddings")
    embedding = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2",
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )
    vectorstore = PostgresVectorStore(
        pg_config=cfg,
        embedding_function=embedding,
        collection_name=collection,
        distance_metric="cosine",
    )
    query = "Rucio probes Pushgateway jobber"
    hits = vectorstore.hybrid_search(query, k=5, semantic_weight=0.4, bm25_weight=0.6)
    compact = []
    for item in hits[:5]:
        doc = item[0] if isinstance(item, tuple) else item
        metadata = getattr(doc, "metadata", {}) or {}
        compact.append({
            "resource_hash": metadata.get("resource_hash"),
            "ticket_id": metadata.get("ticket_id"),
            "source_type": metadata.get("source_type"),
            "snippet": (getattr(doc, "page_content", "") or "")[:160],
        })
    return {"query": query, "hit_count": len(hits), "hits": compact}


def okg_preflight_probe():
    from src.archi.utils.okg_vectorstore import OKGVectorStore, okg_config_from_env

    config = okg_config_from_env()
    vectorstore = OKGVectorStore(config)
    probe = vectorstore.probe(config.probe_query, k=1)
    return {
        **vectorstore.retrieval_metadata,
        "generation_id": probe.get("first_hit", {}).get("okg_generation_id")
        or vectorstore.retrieval_metadata.get("generation_id"),
        "probe": probe,
    }


def socket_state_count(port: int, state: str) -> Optional[int]:
    try:
        proc = subprocess.run(
            ["ss", "-tan", "state", state, f"( sport = :{port} )"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return max(0, len(lines) - 1)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-set", choices=["bare", "rag", "no-tools", "live"], required=True)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    parser.add_argument("--manifest-out")
    parser.add_argument("--require-service-health", action="store_true")
    parser.add_argument(
        "--knowledge-backend",
        choices=["data_manager", "okg"],
        default=os.environ.get(
            "ARCHI_KNOWLEDGE_BACKEND",
            "okg" if os.environ.get("BENCHMARK_TIER") == "orcd-vllm-okg" else "data_manager",
        ),
    )
    args = parser.parse_args()

    failures = []
    contract = load_json(Path(args.contract))
    config = contract["configs"][args.tool_set]
    if args.knowledge_backend == "okg":
        okg_contract = contract.get("okg_tier", {})
        if args.tool_set == "live":
            bound_tools = OKG_MCP_READ_TOOLS + OKG_MCP_LIVE_TOOLS
        else:
            bound_tools = OKG_MCP_READ_TOOLS
        forbidden = okg_contract.get("forbidden_tools", [])
    else:
        bound_tools = config.get("tools") or config.get("required_tools") or []
        forbidden = config.get("forbidden_tools", [])
    questions_path = Path(args.questions)
    if not questions_path.is_absolute():
        questions_path = REPO / questions_path
    questions = load_json(questions_path)
    qids = [f"question_{q.get('idx', q.get('id', i))}" for i, q in enumerate(questions)]
    duplicate_qids = sorted({qid for qid in qids if qids.count(qid) > 1})
    if duplicate_qids:
        fail(failures, f"duplicate qids: {duplicate_qids[:10]}")
    if len(qids) == 270 and qids[-10:] != [f"question_{i}" for i in range(260, 270)]:
        fail(failures, "270Q file does not append aux rows as question_260..question_269")
    question_identity_preview = [
        {
            "idx": q.get("idx"),
            "gold_idx": q.get("gold_idx"),
            "source_question_set": q.get("source_question_set"),
        }
        for q in questions[:3]
    ]
    question_fields = sorted({key for q in questions if isinstance(q, dict) for key in q})

    prompt_checks = {}
    prompt_path = config.get("prompt_path")
    canonical_prompt = {
        "no-tools": "examples/agents/cms-comp-ops-no-live-data.md",
        "live": "examples/agents/cms-comp-ops.md",
    }.get(args.tool_set)
    if canonical_prompt and prompt_path != canonical_prompt:
        fail(failures, f"{args.tool_set} prompt_path={prompt_path!r}, expected {canonical_prompt!r}")
    if prompt_path:
        path = REPO / prompt_path
        text = strip_frontmatter(path.read_text())
        prompt_checks[prompt_path] = sha256_file(path)
        if "read_skill" in text:
            fail(failures, f"{prompt_path} references read_skill")
    for prompt in config.get("prompt_paths", []):
        path = REPO / prompt
        prompt_checks[prompt] = sha256_file(path)

    if "read_skill" not in forbidden and args.tool_set in {"live", "no-tools"}:
        fail(failures, f"{args.tool_set} contract does not forbid read_skill")
    if args.knowledge_backend == "okg":
        live_markers = ("rucio", "monit", "condor", "grep", "catalog", "metadata")
        unexpected = []
        if args.tool_set != "live":
            unexpected = [
                tool for tool in bound_tools
                if any(marker in tool.lower() for marker in live_markers)
            ]
        if unexpected:
            fail(failures, f"OKG no-live run binds non-OKG tools: {unexpected}")

    service = {}
    dm_url = os.environ.get("ARCHI_DM_URL")
    if args.knowledge_backend == "okg":
        if args.tool_set not in {"no-tools", "live"}:
            fail(failures, "OKG benchmark backend is currently supported for tool-set no-tools or live only")
        if args.tool_set == "live":
            token_file = SECRETS_DIR / "monit_grafana_token.txt"
            if not os.environ.get("MONIT_GRAFANA_TOKEN") and not token_file.exists():
                fail(failures, "MONIT_GRAFANA_TOKEN or monit_grafana_token.txt required for OKG live preflight")
        if args.require_service_health:
            try:
                service["okg"] = okg_preflight_probe()
            except Exception as exc:
                fail(failures, f"okg probe failed: {type(exc).__name__}: {exc}")
    elif args.require_service_health:
        if not dm_url:
            fail(failures, "ARCHI_DM_URL required for service health")
        else:
            try:
                service["schema"] = http_json(f"{dm_url.rstrip('/')}/api/catalog/schema", timeout=10)
                service["grep_probe"] = http_json(
                    f"{dm_url.rstrip('/')}/api/catalog/search",
                    params={
                        "q": "CMSTZ-614",
                        "limit": 3,
                        "mode": "grep",
                        "regex": "false",
                        "case_sensitive": "true",
                        "search_content": "true",
                    },
                    timeout=20,
                )
                if not service["grep_probe"].get("hits"):
                    fail(failures, "grep probe returned zero hits for CMSTZ-614")
            except Exception as exc:
                fail(failures, f"data-manager probe failed: {type(exc).__name__}: {exc}")
        try:
            service["postgres"] = pg_counts()
            expected = contract["corpus"]
            for key in ("documents", "document_chunks", "vectorized_hashes", "documents_without_chunks", "orphan_chunks"):
                if service["postgres"].get(key) != expected[key]:
                    fail(failures, f"postgres {key}={service['postgres'].get(key)} expected {expected[key]}")
        except Exception as exc:
            fail(failures, f"postgres counts failed: {type(exc).__name__}: {exc}")
        service["corpus"] = corpus_snapshot()
        expected = contract["corpus"]
        if service["corpus"].get("file_count") != expected["submit75_corpus_file_count"]:
            fail(failures, f"corpus file_count={service['corpus'].get('file_count')} expected {expected['submit75_corpus_file_count']}")
        if service["corpus"].get("filelist_sha256") != expected["submit75_corpus_filelist_sha256"]:
            fail(failures, f"corpus filelist_sha256={service['corpus'].get('filelist_sha256')} expected {expected['submit75_corpus_filelist_sha256']}")
        if config.get("retrieval") != "none":
            try:
                service["vectorstore_probe"] = vectorstore_probe()
                hits = service["vectorstore_probe"].get("hits") or []
                expected_hashes = {"ticket_CMSTRANSF-1247", "ticket_CMSTRANSF-589"}
                if not any((h.get("resource_hash") in expected_hashes) for h in hits):
                    fail(failures, "vectorstore probe did not return expected Rucio probe source documents")
            except Exception as exc:
                fail(failures, f"vectorstore probe failed: {type(exc).__name__}: {exc}")
        if dm_url:
            parsed = urllib.parse.urlparse(dm_url)
            port = parsed.port or 80
            if parsed.hostname in {"127.0.0.1", "localhost", socket.gethostname()}:
                close_wait = socket_state_count(port, "close-wait")
                service["close_wait"] = close_wait
                if close_wait is not None and close_wait > 50:
                    fail(failures, f"port {port} CLOSE-WAIT count {close_wait} exceeds 50")

    manifest = {
        "schema_version": "orcd-vllm-okg/v1" if args.knowledge_backend == "okg" else contract["schema_version"],
        "tier": os.environ.get("BENCHMARK_TIER") or contract["tier"],
        "tool_set": args.tool_set,
        "knowledge_backend": args.knowledge_backend,
        "tools_bound": bound_tools,
        "forbidden_tools": forbidden,
        "contract": str(Path(args.contract)),
        "runtime_limits": {
            **(contract.get("common", {}).get("runtime_limits", {})),
            "effective_concurrency": os.environ.get("CONCURRENCY_OVERRIDE"),
            "effective_max_tool_calls": os.environ.get("MAX_TOOL_CALLS"),
            "effective_tool_timeout_s": os.environ.get("TOOL_TIMEOUT_S"),
            "effective_catalog_http_timeout_s": os.environ.get("CATALOG_HTTP_TIMEOUT_S"),
            "effective_per_question_timeout_s": os.environ.get("PER_QUESTION_TIMEOUT_S"),
        },
        "prompt_sha256": prompt_checks,
        "questions_path": str(questions_path),
        "questions_sha256": sha256_file(questions_path),
        "qid_count": len(qids),
        "qid_first": qids[:3],
        "qid_last": qids[-10:],
        "duplicate_qids": duplicate_qids,
        "question_fields": question_fields,
        "question_identity_preview": question_identity_preview,
        "model": {
            "backend": os.environ.get("LLM_PROVIDER", "vllm"),
            "id": os.environ.get("LLM_MODEL") or os.environ.get("VLLM_MODEL"),
            "vllm_enable_thinking": os.environ.get("VLLM_ENABLE_THINKING"),
        },
        "service": service,
        "failures": failures,
        "created_ts_epoch": int(time.time()),
    }
    if args.manifest_out:
        Path(args.manifest_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.manifest_out).write_text(json.dumps(manifest, indent=2, default=str))
    print(json.dumps(manifest, indent=2, default=str))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
