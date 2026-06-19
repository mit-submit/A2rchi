"""v3 bench driver: idempotent resume + per-tool timeout + comprehensive trace +
4-config support (live | no-tools | rag | bare).

Changes vs v2:
  TIMEOUT_TOOL   per-tool 30s wall (was unbounded — q225 hung in v2)
  TIMEOUT_QUESTION  whole-question wall (was per-LLM-call only in v2 — questions
                    could run 800-900s by chaining many LLM calls)
  IDEMPOTENT     --out path is loaded at start; already-completed (no-error)
                 qids are skipped. Crash + restart resumes exactly where we
                 left off, no duplicates.
  TRACE          every event now stamped with ts_epoch + iter. LLM steps emit
                 their own llm_call events (model, finish_reason, in/out tokens,
                 duration_s, content_chars, n_returned_tool_calls). Budget
                 warnings, forced-final, and parser retries are tracked too.
  TOOL-SETS      live | no-tools | rag | bare  (rag = catalog hits stuffed into
                 a single LLM call; bare = system prompt + question only).
  SEED           fixed seed in LLM extra_body for vLLM determinism.

Same env contract as v2 (ARCHI_DM_URL, VLLM_URL, etc).
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import List, Optional

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI

REPO = Path(os.environ.get("ORCD_REPO", os.path.expanduser("~/A2rchi")))
SCRIPT_DIR = Path(__file__).resolve().parent
SECRETS_DIR = Path(os.environ.get("ARCHI_SECRETS_DIR",
                                  os.path.expanduser("~/.archi-bundle-state/bundle/secrets/archi")))
DEFAULT_QUESTIONS = REPO / "configs/submit75/curated_questions.json"
OUT_DIR = Path(os.environ.get("ORCD_OUT_DIR", os.path.expanduser("~/bench_out/run_260q_orcd_v3")))
OUT_DIR.mkdir(parents=True, exist_ok=True)

RUN_TIER            = os.environ.get("BENCHMARK_TIER", "orcd-vllm-corrected")
KNOWLEDGE_BACKEND   = os.environ.get(
    "ARCHI_KNOWLEDGE_BACKEND",
    "okg" if RUN_TIER == "orcd-vllm-okg" else "data_manager",
)
ARCHI_DM_URL        = os.environ.get("ARCHI_DM_URL")
if KNOWLEDGE_BACKEND != "okg" and not ARCHI_DM_URL:
    sys.exit("ARCHI_DM_URL required")
ARCHI_RUCIO_MCP_URL = os.environ.get("ARCHI_RUCIO_MCP_URL")
VLLM_URL            = os.environ.get("VLLM_URL")
VLLM_MODEL          = os.environ.get("VLLM_MODEL")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

for name in ("openai_api_key", "openrouter_api_key", "monit_grafana_token",
             "jira_pat", "dm_api_token", "git_token", "git_username"):
    f = SECRETS_DIR / f"{name}.txt"
    if f.exists():
        os.environ[name.upper()] = f.read_text().strip()

print(f"REPO: {REPO}")
sys.path.insert(0, str(SCRIPT_DIR))
import agent_tool_helpers as smoke  # noqa: E402
from vectorstore_tool import make_search_vectorstore_hybrid  # noqa: E402

smoke.REPO = REPO
smoke.QUESTIONS = DEFAULT_QUESTIONS

# v3 budget + timeouts
MAX_TOOL_CALLS         = int(os.environ.get("MAX_TOOL_CALLS", "30"))
TOOL_TIMEOUT_S         = int(os.environ.get("TOOL_TIMEOUT_S", "30"))
PER_QUESTION_TIMEOUT_S = int(os.environ.get("PER_QUESTION_TIMEOUT_S", "600"))
LLM_TIMEOUT_S          = int(os.environ.get("LLM_TIMEOUT_S", "200"))
CATALOG_HTTP_TIMEOUT_S = max(1.0, min(float(os.environ.get("CATALOG_HTTP_TIMEOUT_S", "20")), TOOL_TIMEOUT_S - 5.0))
BULK_FETCH_MAX_HASHES  = int(os.environ.get("BULK_FETCH_MAX_HASHES", "8"))
BULK_FETCH_WORKERS     = int(os.environ.get("BULK_FETCH_WORKERS", "4"))
SEED                   = int(os.environ.get("VLLM_SEED", "42"))
OUTPUT_PREVIEW_CHARS   = int(os.environ.get("OUTPUT_PREVIEW_CHARS", "2000"))
TOOL_MESSAGE_MAX_CHARS = int(os.environ.get("TOOL_MESSAGE_MAX_CHARS", str(OUTPUT_PREVIEW_CHARS)))
WARN_FRACTIONS         = [0.5, 0.75, 0.9]
VLLM_ENABLE_THINKING   = _env_bool("VLLM_ENABLE_THINKING", True)
OKG_PARITY_REGULAR_LIVE_SOURCES = _env_bool(
    "ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES",
    False,
)
OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS = _env_bool(
    "ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS",
    True,
)

RUCIO_TOOL_DENYLIST = {"rucio_list_transfer_limits"}
OKG_MCP_READ_TOOLS = {
    "inspect",
    "search",
    "expand",
    "filter",
    "map",
    "aggregate",
    "query",
}
OKG_MCP_LIVE_TOOLS = {
    "cms_monit_rucio_search",
    "cms_monit_rucio_aggregate",
    "cms_monit_condor_search",
    "cms_monit_condor_aggregate",
}
OKG_MCP_CLIENTS = []


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _model_serving_manifest(model_name: str, llm_provider: str) -> dict:
    is_fp8 = "FP8" in (model_name or "").upper() or os.environ.get("VLLM_QUANTIZATION", "").lower() == "fp8"
    return {
        "provider": llm_provider,
        "id": model_name,
        "backend": "vllm-openai-compatible" if llm_provider == "vllm" else llm_provider,
        "base_url": VLLM_URL if llm_provider == "vllm" else None,
        "dtype": os.environ.get("VLLM_DTYPE", "bfloat16" if llm_provider == "vllm" else None),
        "quantization": os.environ.get("VLLM_QUANTIZATION", "fp8" if is_fp8 else None),
        "fp8": is_fp8,
        "tensor_parallel": os.environ.get("VLLM_TENSOR_PARALLEL"),
        "expert_parallel": _env_bool("VLLM_ENABLE_EXPERT_PARALLEL", False),
        "mtp_tokens": int(os.environ.get("VLLM_MTP_TOKENS", "0") or "0"),
        "tool_call_parser": os.environ.get("VLLM_TOOL_CALL_PARSER"),
        "reasoning_parser": os.environ.get("VLLM_REASONING_PARSER"),
        "thinking_enabled": VLLM_ENABLE_THINKING if llm_provider == "vllm" else None,
    }


def _load_agent_prompt(path: Path) -> str:
    agent_text = path.read_text()
    if agent_text.startswith("---"):
        end = agent_text.find("---", 3)
        if end > 0:
            agent_text = agent_text[end + 3:].lstrip()
    return agent_text


async def _collect_rucio_orcd():
    cfg = {"rucio": {"transport": "streamable_http", "url": ARCHI_RUCIO_MCP_URL}}
    client = MultiServerMCPClient(cfg)
    return await client.get_tools()
smoke.collect_rucio_tools = _collect_rucio_orcd


def _build_catalog_orcd():
    catalog = smoke.RemoteCatalogClient(base_url=ARCHI_DM_URL, timeout=CATALOG_HTTP_TIMEOUT_S)
    catalog._headers = {**getattr(catalog, "_headers", {}), "Connection": "close"}
    schema_probe = catalog.schema()
    n_indexed = schema_probe.get("counts", {}).get("documents")
    print(f"  catalog API reachable at {ARCHI_DM_URL}; {n_indexed} indexed docs")
    return [
        smoke.create_grep_tool(catalog, name="grep"),
        smoke.create_metadata_search_tool(catalog, name="search_metadata_index"),
        smoke.create_metadata_schema_tool(catalog, name="list_metadata_schema"),
        smoke.create_document_fetch_tool(
            catalog,
            description="Fetch the full text of one catalogued document by resource hash. "
                        "Use fetch_catalog_documents_bulk when you need multiple documents."),
        _make_bulk_fetch_tool(catalog),
    ], catalog


def _make_bulk_fetch_tool(catalog):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    description = (
        "Fetch many catalogued documents in a single tool call. "
        "Input: resource_hashes (list of strings, max 50), max_chars_per_doc=2000. "
        "Output: documents concatenated with `===` separators."
    )

    @tool("fetch_catalog_documents_bulk", description=description)
    def _bulk(resource_hashes: List[str], max_chars_per_doc: int = 2000) -> str:
        if not isinstance(resource_hashes, list):
            return "ERROR: resource_hashes must be a list of strings."
        hashes = [h.strip() for h in resource_hashes if isinstance(h, str) and h.strip()]
        if not hashes:
            return "ERROR: no non-empty resource hashes provided."
        truncated = False
        if len(hashes) > BULK_FETCH_MAX_HASHES:
            hashes = hashes[:BULK_FETCH_MAX_HASHES]
            truncated = True

        results = {}
        max_workers = max(1, min(BULK_FETCH_WORKERS, len(hashes)))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(catalog.get_document, h, max_chars=max_chars_per_doc): h for h in hashes}
            for fut in as_completed(futs):
                h = futs[fut]
                try:
                    results[h] = fut.result()
                except Exception as e:
                    results[h] = {"_error": f"{type(e).__name__}: {e}"}
        out = [f"Fetched {len(hashes)} documents with max_workers={max_workers}:\n"]
        if truncated:
            out.append(
                f"NOTE: input was truncated to the first {BULK_FETCH_MAX_HASHES} "
                "resource hashes to keep catalog load bounded.\n"
            )
        for h in hashes:
            doc = results.get(h) or {}
            if "_error" in doc:
                out.append(f"=== {h} ===\nERROR: {doc['_error']}\n")
            elif not doc:
                out.append(f"=== {h} ===\n(not found)\n")
            else:
                path = doc.get("path") or ""
                meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
                text = doc.get("text") or ""
                meta_str = "\n".join(f"  {k}: {v}" for k, v in sorted(meta.items())) if meta else "  (no metadata)"
                out.append(f"=== {h} ===\nPath: {path}\nMetadata:\n{meta_str}\nContent:\n{text}\n")
        return "\n".join(out)
    return _bulk


def _okg_retrieval_manifest() -> dict:
    from src.archi.utils.okg_vectorstore import okg_config_from_env

    cfg = okg_config_from_env()
    return {
        "backend": "okg",
        "read_surface": cfg.read_surface,
        "mcp_transport": "stdio",
        "mcp_tool": "search",
        "mcp_tools": sorted(OKG_MCP_READ_TOOLS),
        "mcp_live_tools": sorted(OKG_MCP_LIVE_TOOLS),
        "mcp_command_env": "OKG_MCP_COMMAND",
        "mcp_deployment_name": os.environ.get("OKG_MCP_DEPLOYMENT_NAME"),
        "retrieval_method": cfg.retrieval_method,
        "deployment": cfg.deployment,
        "branch": cfg.branch,
        "generation_id": cfg.generation_id,
        "compat_generation_id": cfg.compat_generation_id,
        "dsn_env": cfg.dsn_env,
        "top_k": cfg.top_k,
        "subtype": cfg.subtype,
    }


def _okg_mcp_server_config() -> dict:
    server_code = (
        "import os\n"
        "from okg.substrate.mcp.server import run_stdio\n"
        "run_stdio(\n"
        "    dsn=os.environ['OKG_DSN'],\n"
        "    deployment_name=os.environ.get('OKG_MCP_DEPLOYMENT_NAME', 'cms'),\n"
        ")\n"
    )
    command = os.environ.get("OKG_MCP_COMMAND") or str(Path.home() / ".local" / "bin" / "uv")
    cwd = os.environ.get("OKG_MCP_CWD") or os.environ.get("CMS_OKG_FRAMEWORK_DIR") or str(Path.home() / "okg")
    deployment = os.environ.get("OKG_MCP_DEPLOYMENT_NAME") or os.environ.get("OKG_DEPLOYMENT") or "cms"
    env = dict(os.environ)
    env.update({
        "OKG_DSN": os.environ.get("OKG_DSN", ""),
        "OKG_MCP_DEPLOYMENT_NAME": deployment,
        "PYTHONUNBUFFERED": "1",
    })
    pythonpath = os.environ.get("OKG_PYTHONPATH")
    if pythonpath:
        env["PYTHONPATH"] = pythonpath
    return {
        "transport": "stdio",
        "command": command,
        "args": ["run", "--extra", "mcp", "python", "-c", server_code],
        "cwd": cwd,
        "env": env,
    }


def _okg_mcp_allowed_tools(*, require_live: bool) -> set[str]:
    raw = os.environ.get("OKG_MCP_TOOL_ALLOWLIST")
    if raw:
        if raw.strip().lower() in {"*", "all"}:
            return set()
        return {item.strip() for item in raw.split(",") if item.strip()}
    tools = set(OKG_MCP_READ_TOOLS)
    if require_live:
        tools.update(OKG_MCP_LIVE_TOOLS)
    return tools


async def _collect_okg_mcp_tools(*, require_live: bool):
    if not os.environ.get("OKG_DSN"):
        sys.exit("OKG_DSN required for OKG MCP benchmark runs")
    cfg = {"okg": _okg_mcp_server_config()}
    client = MultiServerMCPClient(cfg)
    OKG_MCP_CLIENTS.append(client)
    all_tools = await client.get_tools(server_name="okg")
    available = {getattr(tool, "name", "?"): tool for tool in all_tools}
    allowed = _okg_mcp_allowed_tools(require_live=require_live)
    required = set(OKG_MCP_READ_TOOLS)
    if require_live:
        required.update(OKG_MCP_LIVE_TOOLS)
    missing = sorted(required - set(available))
    if missing:
        sys.exit(
            "OKG MCP server did not expose required benchmark tools; "
            f"missing={missing} available={sorted(available)}"
        )
    if not allowed and os.environ.get("OKG_MCP_TOOL_ALLOWLIST", "").strip().lower() in {"*", "all"}:
        tools = [available[name] for name in sorted(available)]
    else:
        tools = [available[name] for name in sorted(allowed & set(available))]
    if not tools:
        sys.exit(
            "OKG MCP server did not expose any allowed benchmark tools; "
            f"allowed={sorted(allowed)} available={sorted(available)}"
        )
    return tools


# ------------ rich trace helpers ------------

def _now() -> int: return int(time.time())

def _event(d: dict) -> dict:
    d.setdefault("ts_epoch", _now())
    return d

def _budget_warning(n_used: int, n_max: int, pct: float) -> str:
    remaining = n_max - n_used
    return (
        f"[SYSTEM-INJECTED BUDGET CHECKPOINT — not a new user turn] "
        f"You have used {n_used}/{n_max} tool calls ({int(pct*100)}% of your "
        f"budget; {remaining} remain). Start narrowing your investigation. "
        f"Avoid pagination loops and redundant queries. If you already have "
        f"enough information, answer the user's ORIGINAL question now instead "
        f"of making more tool calls."
    )

def _forced_final_msg(n_max: int) -> str:
    return (
        f"[SYSTEM-INJECTED FINAL TURN — not a new user turn] "
        f"TOOL BUDGET EXHAUSTED ({n_max}/{n_max}). Produce your final answer "
        f"to the user's ORIGINAL question using only information already gathered. "
        f"Do not request any more tools. Reply in plain text."
    )


def _message_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(text, dict) and isinstance(text.get("value"), str):
                    parts.append(text["value"])
        return "\n\n".join(part for part in parts if part)
    return str(content)


# ------------ pipelines ------------

async def run_agent(qid, qtext, *, llm_factory, tools, system_prompt, max_tool_calls, model_name):
    """React loop with budget control, per-tool timeout, comprehensive trace."""
    t0 = time.time()
    trace_events: list = []
    tool_map = {t.name: t for t in tools}
    messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=qtext),
    ]
    n_tool_calls = 0
    iter_idx = 0
    warnings_issued: set = set()
    llm_with_tools = llm_factory().bind_tools(tools)
    llm_no_tools   = llm_factory()

    async def call_llm(use_tools: bool, iter_idx: int):
        target = llm_with_tools if use_tools else llm_no_tools
        t_llm = time.time()
        try:
            ai = await asyncio.wait_for(target.ainvoke(messages), timeout=LLM_TIMEOUT_S)
            err = None
        except Exception as e:
            estr = str(e)
            # vLLM 'Extra data' / 'Expecting value' JSON parser brittleness — single retry
            if ("Extra data" in estr or "Expecting value" in estr) and use_tools:
                trace_events.append(_event({"type": "parser_retry", "iter": iter_idx, "err": estr[:200]}))
                messages.append(HumanMessage(content=(
                    "[SYSTEM-INJECTED PARSER NUDGE — not a new user turn] "
                    "Your last tool-call output had malformed JSON ('Extra data'). "
                    "Emit one valid JSON object for tool arguments; no trailing text."
                )))
                t_llm = time.time()
                ai = await asyncio.wait_for(target.ainvoke(messages), timeout=LLM_TIMEOUT_S)
                err = None
            else:
                raise
        duration_s = time.time() - t_llm

        # Capture token usage if vLLM passed it back via response_metadata
        usage = (getattr(ai, "usage_metadata", None) or {})
        finish_reason = None
        rm = getattr(ai, "response_metadata", None) or {}
        if isinstance(rm, dict):
            finish_reason = rm.get("finish_reason") or rm.get("stop_reason")
        content = _message_text(getattr(ai, "content", "") or "")
        tool_calls = getattr(ai, "tool_calls", None) or []

        trace_events.append(_event({
            "type": "llm_call",
            "iter": iter_idx,
            "model": model_name,
            "duration_s": duration_s,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "finish_reason": finish_reason,
            "content_chars": len(content) if isinstance(content, str) else None,
            "n_tool_calls_returned": len(tool_calls),
            "with_tools": use_tools,
        }))
        return ai

    try:
        # Whole-question wall timeout
        async def _inner():
            nonlocal n_tool_calls, iter_idx
            okg_tool_required_retry = False
            for _ in range(max_tool_calls * 2 + 5):
                iter_idx += 1

                # budget warnings
                for pct in WARN_FRACTIONS:
                    threshold = int(max_tool_calls * pct)
                    if n_tool_calls >= threshold and pct not in warnings_issued:
                        warnings_issued.add(pct)
                        messages.append(HumanMessage(content=_budget_warning(n_tool_calls, max_tool_calls, pct)))
                        trace_events.append(_event({
                            "type": "budget_warning",
                            "iter": iter_idx, "at_call_n": n_tool_calls,
                            "pct": pct, "max_calls": max_tool_calls,
                        }))

                # forced final
                if n_tool_calls >= max_tool_calls:
                    messages.append(HumanMessage(content=_forced_final_msg(max_tool_calls)))
                    trace_events.append(_event({
                        "type": "budget_forced_final", "iter": iter_idx,
                        "at_call_n": n_tool_calls, "max_calls": max_tool_calls,
                    }))
                    ai = await call_llm(use_tools=False, iter_idx=iter_idx)
                    messages.append(ai)
                    return _message_text(getattr(ai, "content", "") or ""), True

                ai = await call_llm(use_tools=True, iter_idx=iter_idx)
                messages.append(ai)
                tool_calls = getattr(ai, "tool_calls", None) or []
                if not tool_calls:
                    content = _message_text(getattr(ai, "content", "") or "")
                    if KNOWLEDGE_BACKEND == "okg" and n_tool_calls == 0:
                        if okg_tool_required_retry:
                            trace_events.append(_event({
                                "type": "okg_tool_required_failed",
                                "iter": iter_idx,
                                "content_chars": len(content),
                            }))
                            raise RuntimeError(
                                "OKG benchmark question produced a final answer "
                                "without any OKG MCP tool call"
                            )
                        okg_tool_required_retry = True
                        messages.append(HumanMessage(content=(
                            "[SYSTEM-INJECTED OKG TOOL REQUIRED — not a new user turn] "
                            "This is an OKG benchmark run. You must call at least one "
                            "bound OKG MCP tool before producing a final answer. Use "
                            "`inspect` for graph/session/ontology context or `search` "
                            "with a concise lexical query; then answer the user's "
                            "ORIGINAL question from the tool evidence."
                        )))
                        trace_events.append(_event({
                            "type": "okg_tool_required_retry",
                            "iter": iter_idx,
                            "content_chars": len(content),
                        }))
                        continue
                    if content.strip():
                        return content, False
                    messages.append(HumanMessage(content=(
                        "[SYSTEM-INJECTED EMPTY-FINAL REPAIR — not a new user turn] "
                        "Your previous response contained no tool calls and no final answer. "
                        "Produce the final answer to the user's ORIGINAL question now, using "
                        "only information already gathered. Do not request tools."
                    )))
                    iter_idx += 1
                    trace_events.append(_event({
                        "type": "empty_final_repair",
                        "iter": iter_idx,
                        "at_call_n": n_tool_calls,
                    }))
                    ai = await call_llm(use_tools=False, iter_idx=iter_idx)
                    messages.append(ai)
                    return _message_text(getattr(ai, "content", "") or ""), False

                for call_idx, tc in enumerate(tool_calls):
                    tc_name = tc.get("name", "?")
                    tc_args = tc.get("args", {}) or {}
                    tc_id   = tc.get("id") or f"call_{iter_idx}_{call_idx}_{tc_name}"

                    trace_events.append(_event({
                        "type": "tool_call", "iter": iter_idx, "call_idx": call_idx,
                        "tool_name": tc_name, "args": tc_args, "call_id": tc_id,
                    }))

                    tool_obj = tool_map.get(tc_name)
                    t_call_start = time.time()
                    timed_out = False
                    if tool_obj is None:
                        out_str = f"ERROR: unknown tool {tc_name!r}. Available: {sorted(tool_map.keys())}"
                    else:
                        try:
                            result = await asyncio.wait_for(tool_obj.ainvoke(tc_args), timeout=TOOL_TIMEOUT_S)
                            out_str = str(result)
                        except asyncio.TimeoutError:
                            timed_out = True
                            out_str = f"ERROR: tool {tc_name} exceeded {TOOL_TIMEOUT_S}s timeout; result not available."
                        except Exception as e:
                            out_str = f"ERROR calling {tc_name}: {type(e).__name__}: {e}"
                    duration_s = time.time() - t_call_start

                    full_len = len(out_str)
                    preview = out_str[:OUTPUT_PREVIEW_CHARS]
                    truncated = full_len > OUTPUT_PREVIEW_CHARS
                    message_content = out_str[:TOOL_MESSAGE_MAX_CHARS]
                    message_truncated = full_len > TOOL_MESSAGE_MAX_CHARS
                    if message_truncated:
                        message_content += (
                            f"\n\n[TRUNCATED TOOL OUTPUT: {full_len - TOOL_MESSAGE_MAX_CHARS} "
                            "characters omitted. Call a narrower query/tool if more detail is required.]"
                        )
                    trace_events.append(_event({
                        "type": "tool_output", "iter": iter_idx, "call_idx": call_idx,
                        "tool_name": tc_name, "call_id": tc_id,
                        "output_preview": preview + ("..." if truncated else ""),
                        "output_chars": full_len, "truncated": truncated,
                        "message_chars": len(message_content),
                        "message_truncated": message_truncated,
                        "duration_s": duration_s, "timed_out": timed_out,
                    }))
                    messages.append(ToolMessage(content=message_content, tool_call_id=tc_id, name=tc_name))
                    n_tool_calls += 1

                    # cap may have just been crossed by parallel tool calls — break to next iter
                    if n_tool_calls >= max_tool_calls:
                        break

            messages.append(HumanMessage(content=(
                "[SYSTEM-INJECTED ITERATION-CAP FINAL — not a new user turn] "
                "The agent loop reached its iteration cap. Produce the final answer "
                "to the user's ORIGINAL question using only information already gathered. "
                "Do not request tools."
            )))
            iter_idx += 1
            trace_events.append(_event({
                "type": "iteration_cap_forced_final",
                "iter": iter_idx,
                "at_call_n": n_tool_calls,
                "max_calls": max_tool_calls,
            }))
            ai = await call_llm(use_tools=False, iter_idx=iter_idx)
            messages.append(ai)
            return _message_text(getattr(ai, "content", "") or ""), False

        answer, hit_budget = await asyncio.wait_for(_inner(), timeout=PER_QUESTION_TIMEOUT_S)
        return _finalize(qid, qtext, answer, trace_events, t0, error=None,
                         hit_budget=hit_budget, n_tool_calls=n_tool_calls, n_iters=iter_idx,
                         pipeline="agent_v3", model_name=model_name)
    except asyncio.TimeoutError:
        return _finalize(qid, qtext, "", trace_events, t0,
                         error=f"per-question timeout {PER_QUESTION_TIMEOUT_S}s",
                         n_tool_calls=n_tool_calls, n_iters=iter_idx, pipeline="agent_v3",
                         model_name=model_name)
    except Exception as e:
        return _finalize(qid, qtext, "", trace_events, t0,
                         error=f"{type(e).__name__}: {e}", tb=traceback.format_exc(),
                         n_tool_calls=n_tool_calls, n_iters=iter_idx, pipeline="agent_v3",
                         model_name=model_name)


# bare and rag live in run_qa.py (uses real QAPipeline + BareLLMPipeline)


def _finalize(qid, qtext, answer, trace_events, t0, *, error, tb=None,
              hit_budget=False, n_tool_calls=None, n_iters=None, pipeline=None,
              n_retrieval_hits=None, model_name=None):
    out = {
        "question": qtext,
        "answer": answer,
        "time_elapsed": time.time() - t0,
        "model_used": model_name or VLLM_MODEL or "?",
        "model_backend": os.environ.get("LLM_PROVIDER", "vllm"),
        "thinking_enabled": VLLM_ENABLE_THINKING,
        "run_tier": RUN_TIER,
        "pipeline_used": pipeline or "?",
        "trace_events": trace_events,
        "sources_metadata": [],
        "error": error,
        "completed_ts_epoch": _now(),
    }
    if hit_budget: out["hit_budget"] = True
    if n_tool_calls is not None: out["n_tool_calls"] = n_tool_calls
    if n_iters is not None: out["n_iters"] = n_iters
    if n_retrieval_hits is not None: out["n_retrieval_hits"] = n_retrieval_hits
    if tb: out["traceback"] = tb
    return out


# ------------ main ------------

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--questions", type=str, default=str(DEFAULT_QUESTIONS),
                        help="Question JSON file. Defaults to the canonical 260-question set.")
    parser.add_argument("--out", type=str, default=None,
                        help="Output file. If exists, completed (no-error) qids are skipped (idempotent resume).")
    parser.add_argument("--tool-set", choices=["live", "no-tools"], default="live",
                        help="Agent-loop driver only. For rag/bare use scripts/paper_benchmark/run_qa.py.")
    parser.add_argument("--concurrency", type=int,
                        default=int(os.environ.get("CONCURRENCY_OVERRIDE", "32")))
    parser.add_argument("--max-tool-calls", type=int, default=MAX_TOOL_CALLS)
    parser.add_argument("--retry-errored", action="store_true",
                        help="On resume, also retry questions whose prior run errored (default: skip them too).")
    parser.add_argument("--retry-empty", action="store_true",
                        help="On resume, also retry questions whose prior answer is empty.")
    parser.add_argument("--llm-provider", choices=["vllm", "openai", "openrouter"],
                        default=os.environ.get("LLM_PROVIDER", "vllm"),
                        help="LLM backend. Defaults to vllm to preserve Qwen runs.")
    parser.add_argument("--model", default=os.environ.get("LLM_MODEL"),
                        help="Model id for the chosen provider.")
    parser.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL"),
                        help="Optional OpenAI-compatible base URL.")
    parser.add_argument("--api-key-env", default=os.environ.get("LLM_API_KEY_ENV"),
                        help="Environment variable holding the API key for API providers.")
    parser.add_argument("--reasoning-effort", default=os.environ.get("OPENAI_REASONING_EFFORT", "high"))
    parser.add_argument("--use-responses-api", dest="use_responses_api", action="store_true",
                        default=_env_bool("OPENAI_USE_RESPONSES_API", True))
    parser.add_argument("--no-use-responses-api", dest="use_responses_api", action="store_false")
    parser.add_argument("--preflight-only", action="store_true",
                        help="Bind prompts and tools, verify catalog/live-tool setup, then exit without answering questions.")
    args = parser.parse_args()

    llm_provider = args.llm_provider.lower()
    if llm_provider == "vllm":
        model_name = args.model or VLLM_MODEL or sys.exit("VLLM_MODEL required for --llm-provider vllm")
        base_url = args.base_url or VLLM_URL or sys.exit("VLLM_URL required for --llm-provider vllm")
        api_key = "EMPTY"
        api_key_env = None
    elif llm_provider == "openai":
        model_name = args.model or os.environ.get("OPENAI_MODEL") or "gpt-5.5-2026-04-23"
        base_url = args.base_url
        api_key_env = args.api_key_env or "OPENAI_API_KEY"
        api_key = os.environ.get(api_key_env) or sys.exit(f"{api_key_env} required for --llm-provider openai")
    elif llm_provider == "openrouter":
        model_name = args.model or os.environ.get("OPENROUTER_MODEL") or "openai/gpt-5.5"
        base_url = args.base_url or "https://openrouter.ai/api/v1"
        api_key_env = args.api_key_env or "OPENROUTER_API_KEY"
        api_key = os.environ.get(api_key_env) or sys.exit(f"{api_key_env} required for --llm-provider openrouter")
        args.use_responses_api = False
    else:
        raise ValueError(f"unknown llm provider {llm_provider!r}")

    questions_file = Path(args.questions)
    out_file = Path(args.out) if args.out else OUT_DIR / f"results_v3_{args.tool_set}_{int(time.time())}.json"
    print(f"Output: {out_file}")
    print(f"Questions: {questions_file}")
    print(f"tool_set={args.tool_set}  concurrency={args.concurrency}  max_tool_calls={args.max_tool_calls}")
    print(f"llm_provider={llm_provider}  model={model_name}")
    print(f"timeouts: tool={TOOL_TIMEOUT_S}s  llm={LLM_TIMEOUT_S}s  question={PER_QUESTION_TIMEOUT_S}s")
    print(
        f"catalog_http_timeout={CATALOG_HTTP_TIMEOUT_S}s  "
        f"bulk_fetch_max_hashes={BULK_FETCH_MAX_HASHES}  bulk_fetch_workers={BULK_FETCH_WORKERS}"
    )
    print(f"seed={SEED}  preview_chars={OUTPUT_PREVIEW_CHARS}")
    print(f"tool_message_max_chars={TOOL_MESSAGE_MAX_CHARS}")

    # ---- idempotent resume ----
    existing_results = {}
    if out_file.exists():
        try:
            existing = json.load(open(out_file))
            existing_results = existing.get("benchmarking_results", [{}])[0].get("single_question_results", {})
            print(f"  resume: loaded {len(existing_results)} prior results from {out_file}")
        except Exception as e:
            print(f"  resume: failed to load existing results ({e}); starting fresh")

    def should_skip(qid):
        prev = existing_results.get(qid)
        if not prev: return False
        if prev.get("error") and args.retry_errored: return False
        if args.retry_empty and not (prev.get("answer") or "").strip(): return False
        return True

    # ---- system prompt + tools ----
    prompt_path = (
        REPO / "examples/agents/cms-comp-ops-no-live-data.md"
        if args.tool_set == "no-tools"
        else REPO / "examples/agents/cms-comp-ops.md"
    )
    agent_md = _load_agent_prompt(prompt_path)
    if "read_skill" in agent_md:
        sys.exit(f"prompt {prompt_path} references read_skill, which is not bound in the corrected ORCD tier")
    if args.tool_set == "live":
        skill_texts = []
        for name, meta in smoke.SKILL_REGISTRY.items():
            skill_texts.append(f"## SKILL: {name}\n\n{meta['path'].read_text()}\n")
        system_prompt = (
            f"{agent_md}\n\n---\n\n# Inline live-tool reference sections\n\n"
            f"Use these inline references when calling the matching tools.\n\n"
            + "\n".join(skill_texts)
        )
    else:
        system_prompt = agent_md
    if KNOWLEDGE_BACKEND == "okg":
        if args.tool_set == "live" and OKG_PARITY_REGULAR_LIVE_SOURCES:
            if OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS:
                live_note = (
                    " This is a source-parity run against the regular Archi live "
                    "baseline: the regular Archi Rucio MCP tools, MONIT OpenSearch "
                    "tools, catalog tools, and vectorstore tool are also bound. Use "
                    "the OKG MCP graph tools for graph reasoning and the regular "
                    "Archi live-source tools when the question needs the exact live "
                    "source surface available to `regular_archi_live`."
                )
            else:
                live_note = (
                    " This is a strict corpus-parity run: regular Archi Rucio MCP "
                    "and MONIT OpenSearch live tools are bound, but regular Archi "
                    "catalog/vectorstore corpus tools are intentionally not bound. "
                    "Use OKG MCP graph tools for document/corpus retrieval and the "
                    "regular live tools only for current operational state."
                )
        else:
            live_note = (
                " CMS live operating tools are also bound through the OKG MCP server "
                "as `cms_monit_rucio_search`, `cms_monit_rucio_aggregate`, "
                "`cms_monit_condor_search`, and `cms_monit_condor_aggregate`; use them "
                "only when the question asks for current or operational state."
                if args.tool_set == "live"
                else " Do not call live Rucio, MONIT, or Condor tools in this run."
            )
        if not OKG_PARITY_REGULAR_LIVE_SOURCES:
            regular_tool_note = (
                " Do not call grep, metadata search, catalog fetch tools, "
                "`search_vectorstore_hybrid`, or non-OKG Archi live tools in this run."
            )
        elif not OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS:
            regular_tool_note = (
                " Do not call grep, metadata search, catalog fetch tools, or "
                "`search_vectorstore_hybrid`; those regular Archi corpus tools "
                "are intentionally not bound in this strict OKG corpus-parity run."
            )
        else:
            regular_tool_note = ""
        system_prompt += (
            "\n\nThis OKG benchmark run supersedes any corpus-search wording above: "
            "the bound graph tools are the OKG MCP operator tools served over stdio "
            "by the CMS OKG deployment: `inspect`, `search`, `expand`, `filter`, "
            "`map`, `aggregate`, and `query`. Start with `inspect` when you need "
            "graph or ontology context. Call `search` with a concise natural-language "
            "query, `method=\"lexical\"`, and a small `limit`; use the returned "
            "`generation_id` as the graph pin for follow-up OKG calls. Use `expand`, "
            "`filter`, `map`, `aggregate`, and bounded read-only `query` for graph "
            f"reasoning after you have concrete nodes or schemas.{regular_tool_note}"
            f"{live_note}"
        )
    print(f"  system prompt: {len(system_prompt)} chars")

    if (
        KNOWLEDGE_BACKEND == "okg"
        and (not OKG_PARITY_REGULAR_LIVE_SOURCES or not OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS)
    ):
        catalog_tools, catalog = [], None
        vectorstore_tool = None
    else:
        catalog_tools, catalog = _build_catalog_orcd()
        vectorstore_tool = make_search_vectorstore_hybrid(catalog)

    tools = []
    if KNOWLEDGE_BACKEND == "okg":
        okg_tools = await _collect_okg_mcp_tools(require_live=args.tool_set == "live")
        if args.tool_set == "no-tools":
            tools = okg_tools
            print(f"  tools: no-tools → OKG MCP tools {[t.name for t in tools]}")
        elif args.tool_set == "live":
            if OKG_PARITY_REGULAR_LIVE_SOURCES:
                if not ARCHI_RUCIO_MCP_URL:
                    sys.exit("ARCHI_RUCIO_MCP_URL required for OKG parity regular-live sources")
                rucio_tools_all = await smoke.collect_rucio_tools()
                rucio_tools = [t for t in rucio_tools_all if t.name not in RUCIO_TOOL_DENYLIST]
                monit_tools = smoke.build_monit_tools()
                if not rucio_tools:
                    sys.exit("no Rucio MCP tools were collected for OKG parity regular-live sources")
                if not monit_tools:
                    sys.exit("no MONIT/OpenSearch tools were wired for OKG parity regular-live sources")
                regular_live_tools = rucio_tools + monit_tools
                if OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS:
                    regular_live_tools = regular_live_tools + catalog_tools + [vectorstore_tool]
                tools = okg_tools + regular_live_tools
                tool_counts = {
                    "okg": len(okg_tools),
                    "rucio": len(rucio_tools),
                    "monit": len(monit_tools),
                    "catalog": len(catalog_tools) if OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS else 0,
                    "vectorstore": 1 if OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS else 0,
                }
                print(
                    "  tools: live → OKG MCP + regular Archi live parity "
                    f"{tool_counts}; total={len(tools)}"
                )
            else:
                tools = okg_tools
                print(f"  tools: live → OKG MCP tools {[t.name for t in tools]}")
        else:
            sys.exit("OKG benchmark backend is currently supported for --tool-set no-tools or live")
    elif args.tool_set == "live":
        if not ARCHI_RUCIO_MCP_URL:
            sys.exit("ARCHI_RUCIO_MCP_URL required for --tool-set live")
        rucio_tools_all = await smoke.collect_rucio_tools()
        rucio_tools = [t for t in rucio_tools_all if t.name not in RUCIO_TOOL_DENYLIST]
        monit_tools = smoke.build_monit_tools()
        if not rucio_tools:
            sys.exit("no Rucio MCP tools were collected for --tool-set live")
        if not monit_tools:
            sys.exit("no MONIT/OpenSearch tools were wired for --tool-set live")
        # Add the vectorstore tool to the live agent's toolset — matches the
        # The corrected paper "live" config includes this source-search tool.
        tools = rucio_tools + monit_tools + catalog_tools + [vectorstore_tool]
        print(f"  tools: live → {len(tools)} total (rucio={len(rucio_tools)}, monit={len(monit_tools)}, catalog={len(catalog_tools)}, vectorstore=1)")
    elif args.tool_set == "no-tools":
        # Source-backed retrieval without live MONIT or Rucio MCP.
        tools = catalog_tools + [vectorstore_tool]
        print(f"  tools: no-tools → catalog + vectorstore, {len(tools)} total")
    tool_names = [t.name for t in tools]
    if "read_skill" in tool_names:
        sys.exit("read_skill is bound in the corrected ORCD tier")
    duplicate_tool_names = sorted({name for name in tool_names if tool_names.count(name) > 1})
    if duplicate_tool_names:
        sys.exit(f"duplicate tool names bound: {duplicate_tool_names}")

    questions = json.loads(questions_file.read_text())
    effective_qids = [f"question_{q.get('idx', q.get('id', i))}" for i, q in enumerate(questions)]
    if args.preflight_only:
        print(
            f"  preflight-only: ok tool_set={args.tool_set} "
            f"tools={len(tool_names)} questions={len(questions)} "
            f"qid_duplicates={sorted({qid for qid in effective_qids if effective_qids.count(qid) > 1})}"
        )
        return

    retrieval_manifest = (
        _okg_retrieval_manifest()
        if KNOWLEDGE_BACKEND == "okg"
        else {
            "backend": "data_manager",
            "catalog_api": ARCHI_DM_URL,
            "catalog_tool": "grep",
            "hybrid_tool": "search_vectorstore_hybrid",
        }
    )
    if KNOWLEDGE_BACKEND == "okg" and OKG_PARITY_REGULAR_LIVE_SOURCES:
        retrieval_manifest["regular_archi_live_source_parity"] = {
            "enabled": True,
            "catalog_api": ARCHI_DM_URL,
            "rucio_mcp_url": ARCHI_RUCIO_MCP_URL,
            "regular_archi_corpus_tools_bound": OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS,
            "catalog_tools": (
                ["grep", "search_metadata_index", "list_metadata_schema", "fetch_catalog_document", "fetch_catalog_documents_bulk"]
                if OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS else []
            ),
            "hybrid_tool": "search_vectorstore_hybrid" if OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS else None,
            "monit_tools": [
                "rucio_events_search",
                "rucio_events_aggregation",
                "fetch_rucio_document",
                "condor_metric_search",
                "condor_metric_aggregation",
                "fetch_condor_document",
            ],
        }

    run_manifest = {
        "schema_version": "orcd-vllm-okg/v1" if KNOWLEDGE_BACKEND == "okg" else "orcd-vllm-corrected/v1",
        "tier": RUN_TIER,
        "runner": "scripts/paper_benchmark/run_agent.py",
        "tool_set": args.tool_set,
        "prompt_path": str(prompt_path.relative_to(REPO)),
        "prompt_sha256": _sha256_file(prompt_path),
        "model_backend": llm_provider,
        "model_id": model_name,
        "base_url": base_url,
        "openai_use_responses_api": args.use_responses_api if llm_provider == "openai" else None,
        "openai_reasoning_effort": args.reasoning_effort if llm_provider == "openai" else None,
        "thinking_enabled": VLLM_ENABLE_THINKING if llm_provider == "vllm" else bool(args.reasoning_effort),
        "model_serving": _model_serving_manifest(model_name, llm_provider),
        "tools": tool_names,
        "retrieval": retrieval_manifest,
        "questions_path": str(questions_file),
        "questions_sha256": _sha256_file(questions_file),
        "qid_count": len(effective_qids),
        "qid_duplicates": sorted({qid for qid in effective_qids if effective_qids.count(qid) > 1}),
        "runtime_limits": {
            "concurrency": args.concurrency,
            "max_tool_calls": args.max_tool_calls,
            "tool_timeout_s": TOOL_TIMEOUT_S,
            "catalog_http_timeout_s": CATALOG_HTTP_TIMEOUT_S,
            "llm_timeout_s": LLM_TIMEOUT_S,
            "per_question_timeout_s": PER_QUESTION_TIMEOUT_S,
            "tool_message_max_chars": TOOL_MESSAGE_MAX_CHARS,
            "budget_warning_fractions": WARN_FRACTIONS,
            "forced_final_on_budget": True,
        },
    }

    def llm_factory():
        timeout = httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0)
        async_http_client = httpx.AsyncClient(timeout=timeout)
        kwargs = dict(
            model=model_name, api_key=api_key,
            timeout=LLM_TIMEOUT_S, max_retries=1,
            http_async_client=async_http_client,
        )
        if not model_name.startswith("gpt-5.3"):
            kwargs["temperature"] = 0
        if base_url:
            kwargs["base_url"] = base_url
        if llm_provider == "vllm":
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": VLLM_ENABLE_THINKING},
                "seed": SEED,
            }
        elif llm_provider == "openai":
            if args.use_responses_api:
                kwargs["use_responses_api"] = True
            if args.reasoning_effort:
                kwargs["reasoning_effort"] = args.reasoning_effort
        return ChatOpenAI(**kwargs)

    if args.limit:
        questions = questions[args.start:args.start + args.limit]
    elif args.start:
        questions = questions[args.start:]

    todo = []
    skipped = 0
    for i, q in enumerate(questions):
        qidx = q.get("idx", q.get("id", i))
        qid = f"question_{qidx}"
        if should_skip(qid):
            skipped += 1
            continue
        todo.append((i, q, qid))
    print(f"  questions: total={len(questions)} done_in_prior_run={skipped} todo={len(todo)}")

    # carry forward existing results
    single_q_results = dict(existing_results)
    save_lock = asyncio.Lock()

    async def process_one(i, q, qid):
        qtext = q.get("question", q.get("text", ""))
        print(f"--- [{i+1}/{len(questions)}] START {qid}: {qtext[:80]}", flush=True)
        result = await run_agent(qid, qtext,
                                 llm_factory=llm_factory, tools=tools,
                                 system_prompt=system_prompt,
                                 max_tool_calls=args.max_tool_calls,
                                 model_name=model_name)

        n_tool = result.get("n_tool_calls", sum(1 for e in result["trace_events"] if e["type"] == "tool_call"))
        tag = "BUDGET" if result.get("hit_budget") else ("ERR" if result.get("error") else "OK")
        print(f"--- [{i+1}/{len(questions)}] DONE  {qid} {tag}: "
              f"time={result['time_elapsed']:.1f}s tools={n_tool}", flush=True)
        if result.get("error"):
            print(f"      ERROR: {result['error']}", flush=True)
        async with save_lock:
            single_q_results[qid] = result
            out_file.write_text(json.dumps(
                {"run_manifest": run_manifest,
                 "benchmarking_results": [{"single_question_results": single_q_results}]},
                indent=2, default=str))
        return qid

    if args.concurrency <= 1:
        for i, q, qid in todo:
            await process_one(i, q, qid)
    else:
        sem = asyncio.Semaphore(args.concurrency)
        async def bounded(i, q, qid):
            async with sem:
                return await process_one(i, q, qid)
        print(f"=== running {len(todo)} questions with concurrency={args.concurrency} ===", flush=True)
        await asyncio.gather(*(bounded(i, q, qid) for i, q, qid in todo))

    out_file.write_text(json.dumps(
        {"run_manifest": run_manifest,
         "benchmarking_results": [{"single_question_results": single_q_results}]},
        indent=2, default=str))
    print(f"=== complete; results at {out_file} ===", flush=True)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    exit_code = 0
    try:
        loop.run_until_complete(main())
    except BaseException:
        traceback.print_exc()
        exit_code = 1
    finally:
        # Force exit before asyncio waits for executor threads. Tool timeouts
        # can leave synchronous requests stuck after the result JSON is written.
        os._exit(exit_code)
