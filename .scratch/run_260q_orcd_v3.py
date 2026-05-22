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
from __future__ import annotations

import argparse
import asyncio
import importlib.util
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
SECRETS_DIR = Path(os.environ.get("ARCHI_SECRETS_DIR",
                                  os.path.expanduser("~/.archi-bundle-state/bundle/secrets/archi")))
DEFAULT_QUESTIONS = REPO / "configs/submit75/curated_questions.json"
OUT_DIR = Path(os.environ.get("ORCD_OUT_DIR", os.path.expanduser("~/bench_out/run_260q_orcd_v3")))
OUT_DIR.mkdir(parents=True, exist_ok=True)

ARCHI_DM_URL        = os.environ.get("ARCHI_DM_URL")        or sys.exit("ARCHI_DM_URL required")
ARCHI_RUCIO_MCP_URL = os.environ.get("ARCHI_RUCIO_MCP_URL") or sys.exit("ARCHI_RUCIO_MCP_URL required")
VLLM_URL   = os.environ.get("VLLM_URL")   or sys.exit("VLLM_URL required")
VLLM_MODEL = os.environ.get("VLLM_MODEL") or sys.exit("VLLM_MODEL required")

for name in ("openai_api_key", "openrouter_api_key", "monit_grafana_token",
             "jira_pat", "dm_api_token", "git_token", "git_username"):
    f = SECRETS_DIR / f"{name}.txt"
    if f.exists():
        os.environ[name.upper()] = f.read_text().strip()

print(f"REPO: {REPO}")
spec = importlib.util.spec_from_file_location("smoke", str(REPO / ".scratch/run_aux_q10_smoke.py"))
smoke = importlib.util.module_from_spec(spec)
smoke.__dict__["REPO"] = REPO
spec.loader.exec_module(smoke)
smoke.QUESTIONS = DEFAULT_QUESTIONS

# Vectorstore hybrid-search tool (BM25 + semantic) for the RAG config.
# Matches the convention in .scratch/run_aux_q10_all_configs.py.
vs_spec = importlib.util.spec_from_file_location(
    "vs_tool", str(REPO / ".scratch/build_vectorstore_tool.py")
)
vs_mod = importlib.util.module_from_spec(vs_spec)
vs_spec.loader.exec_module(vs_mod)
make_search_vectorstore_hybrid = vs_mod.make_search_vectorstore_hybrid

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
WARN_FRACTIONS         = [0.5, 0.75, 0.9]

RUCIO_TOOL_DENYLIST = {"rucio_list_transfer_limits"}


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


# ------------ pipelines ------------

async def run_agent(qid, qtext, *, llm_factory, tools, system_prompt, max_tool_calls):
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
        content = getattr(ai, "content", "") or ""
        tool_calls = getattr(ai, "tool_calls", None) or []

        trace_events.append(_event({
            "type": "llm_call",
            "iter": iter_idx,
            "model": VLLM_MODEL,
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
                    return getattr(ai, "content", "") or "", True

                ai = await call_llm(use_tools=True, iter_idx=iter_idx)
                messages.append(ai)
                tool_calls = getattr(ai, "tool_calls", None) or []
                if not tool_calls:
                    return getattr(ai, "content", "") or "", False

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
                    trace_events.append(_event({
                        "type": "tool_output", "iter": iter_idx, "call_idx": call_idx,
                        "tool_name": tc_name, "call_id": tc_id,
                        "output_preview": preview + ("..." if truncated else ""),
                        "output_chars": full_len, "truncated": truncated,
                        "duration_s": duration_s, "timed_out": timed_out,
                    }))
                    messages.append(ToolMessage(content=out_str, tool_call_id=tc_id, name=tc_name))
                    n_tool_calls += 1

                    # cap may have just been crossed by parallel tool calls — break to next iter
                    if n_tool_calls >= max_tool_calls:
                        break

            return "", False  # iteration cap

        answer, hit_budget = await asyncio.wait_for(_inner(), timeout=PER_QUESTION_TIMEOUT_S)
        return _finalize(qid, qtext, answer, trace_events, t0, error=None,
                         hit_budget=hit_budget, n_tool_calls=n_tool_calls, n_iters=iter_idx,
                         pipeline="agent_v3")
    except asyncio.TimeoutError:
        return _finalize(qid, qtext, "", trace_events, t0,
                         error=f"per-question timeout {PER_QUESTION_TIMEOUT_S}s",
                         n_tool_calls=n_tool_calls, n_iters=iter_idx, pipeline="agent_v3")
    except Exception as e:
        return _finalize(qid, qtext, "", trace_events, t0,
                         error=f"{type(e).__name__}: {e}", tb=traceback.format_exc(),
                         n_tool_calls=n_tool_calls, n_iters=iter_idx, pipeline="agent_v3")


# bare and rag live in run_260q_orcd_qa.py (uses real QAPipeline + BareLLMPipeline)


def _finalize(qid, qtext, answer, trace_events, t0, *, error, tb=None,
              hit_budget=False, n_tool_calls=None, n_iters=None, pipeline=None,
              n_retrieval_hits=None):
    out = {
        "question": qtext,
        "answer": answer,
        "time_elapsed": time.time() - t0,
        "model_used": VLLM_MODEL,
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
                        help="Agent-loop driver only. For rag/bare use .scratch/run_260q_orcd_qa.py.")
    parser.add_argument("--concurrency", type=int,
                        default=int(os.environ.get("CONCURRENCY_OVERRIDE", "32")))
    parser.add_argument("--max-tool-calls", type=int, default=MAX_TOOL_CALLS)
    parser.add_argument("--retry-errored", action="store_true",
                        help="On resume, also retry questions whose prior run errored (default: skip them too).")
    args = parser.parse_args()

    questions_file = Path(args.questions)
    out_file = Path(args.out) if args.out else OUT_DIR / f"results_v3_{args.tool_set}_{int(time.time())}.json"
    print(f"Output: {out_file}")
    print(f"Questions: {questions_file}")
    print(f"tool_set={args.tool_set}  concurrency={args.concurrency}  max_tool_calls={args.max_tool_calls}")
    print(f"timeouts: tool={TOOL_TIMEOUT_S}s  llm={LLM_TIMEOUT_S}s  question={PER_QUESTION_TIMEOUT_S}s")
    print(
        f"catalog_http_timeout={CATALOG_HTTP_TIMEOUT_S}s  "
        f"bulk_fetch_max_hashes={BULK_FETCH_MAX_HASHES}  bulk_fetch_workers={BULK_FETCH_WORKERS}"
    )
    print(f"seed={SEED}  preview_chars={OUTPUT_PREVIEW_CHARS}")

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
        return True

    # ---- system prompt + tools ----
    agent_md = smoke.load_system_prompt()
    skill_texts = []
    for name, meta in smoke.SKILL_REGISTRY.items():
        skill_texts.append(f"## SKILL: {name}\n\n{meta['path'].read_text()}\n")
    system_prompt = (
        f"{agent_md}\n\n---\n\n# Pre-loaded skill references\n\n"
        f"Use these inline references when calling the matching tools. "
        f"There is NO read_skill tool — the skills below are already loaded.\n\n"
        + "\n".join(skill_texts)
    )
    print(f"  system prompt: {len(system_prompt)} chars")

    catalog_tools, catalog = _build_catalog_orcd()
    vectorstore_tool = make_search_vectorstore_hybrid(catalog)

    tools = []
    if args.tool_set == "live":
        rucio_tools_all = await smoke.collect_rucio_tools()
        rucio_tools = [t for t in rucio_tools_all if t.name not in RUCIO_TOOL_DENYLIST]
        monit_tools = smoke.build_monit_tools()
        # Add the vectorstore tool to the live agent's toolset — matches the
        # run_aux_q10_all_configs.py "live" config which includes it.
        tools = rucio_tools + monit_tools + catalog_tools + [vectorstore_tool]
        print(f"  tools: live → {len(tools)} total (rucio={len(rucio_tools)}, monit={len(monit_tools)}, catalog={len(catalog_tools)}, vectorstore=1)")
    elif args.tool_set == "no-tools":
        # Catalog + vectorstore (BM25+semantic) — no live MONIT, no Rucio MCP.
        tools = catalog_tools + [vectorstore_tool]
        print(f"  tools: no-tools → catalog + vectorstore, {len(tools)} total")

    def llm_factory():
        timeout = httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0)
        async_http_client = httpx.AsyncClient(timeout=timeout)
        extra_body = {
            "chat_template_kwargs": {"enable_thinking": False},
            "seed": SEED,
        }
        return ChatOpenAI(
            model=VLLM_MODEL, api_key="EMPTY", base_url=VLLM_URL,
            temperature=0, timeout=LLM_TIMEOUT_S, max_retries=1,
            http_async_client=async_http_client, extra_body=extra_body,
        )

    questions = json.loads(questions_file.read_text())
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
                                 max_tool_calls=args.max_tool_calls)

        n_tool = result.get("n_tool_calls", sum(1 for e in result["trace_events"] if e["type"] == "tool_call"))
        tag = "BUDGET" if result.get("hit_budget") else ("ERR" if result.get("error") else "OK")
        print(f"--- [{i+1}/{len(questions)}] DONE  {qid} {tag}: "
              f"time={result['time_elapsed']:.1f}s tools={n_tool}", flush=True)
        if result.get("error"):
            print(f"      ERROR: {result['error']}", flush=True)
        async with save_lock:
            single_q_results[qid] = result
            out_file.write_text(json.dumps(
                {"benchmarking_results": [{"single_question_results": single_q_results}]},
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
        {"benchmarking_results": [{"single_question_results": single_q_results}]},
        indent=2, default=str))
    print(f"=== complete; results at {out_file} ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
    # Force exit so hung threads / wedged httpx connections don't keep the
    # process alive past final result write (SLURM dep chain needs us to exit).
    os._exit(0)
