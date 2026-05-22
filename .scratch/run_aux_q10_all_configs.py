"""Run the 10 tool-use questions across multiple paper configs.

Each config has its own (model, tool_set, pipeline_label) tuple:

  live      → full agent (Rucio MCP + MONIT + catalog + grep + vectorstore_hybrid)
              + read_skill (lazy load)
  no-tools  → catalog + grep + vectorstore_hybrid + read_skill, NO MONIT, NO Rucio MCP
  rag       → only search_vectorstore_hybrid (single-shot RAG control)

The framework matches run_aux_q10_smoke.py exactly (same auto-inject wrapper,
same hardened httpx, same per-question agent rebuild). Output for each config
lands at bench_out/aux_q10_smoke/<config-slug>.json.

Usage:
    uv run python .scratch/run_aux_q10_all_configs.py --configs gpt-5.5/live qwen3.6-27b/live
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

import httpx
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

REPO = Path("/Users/jason/projects/A2rchi")
load_dotenv(REPO / ".env")

# Reuse the smoke module wholesale — it has all the tool-build helpers,
# auto-inject wrapper, run_one_question, etc.
spec = importlib.util.spec_from_file_location("smoke", str(REPO / ".scratch/run_aux_q10_smoke.py"))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)

# And the vectorstore helper we just minted
vs_spec = importlib.util.spec_from_file_location("vs", str(REPO / ".scratch/build_vectorstore_tool.py"))
vs_mod = importlib.util.module_from_spec(vs_spec)
vs_spec.loader.exec_module(vs_mod)

OUT_DIR = REPO / "bench_out/aux_q10_smoke"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Config matrix --------------------------------------------------------
# (config_slug, openrouter_model, pipeline_label, tool_set)
# tool_set values: 'live' / 'no-tools' / 'rag'
CONFIGS = {
    "gpt-5.5/live": {
        "model":        "openai/gpt-5.5",
        "pipeline":     "CMSCompOpsAgent (full live tools)",
        "tool_set":     "live",
        "out_file":     "config_gpt-5.5_live.json",
    },
    "qwen3.6-27b/live": {
        "model":        "qwen/qwen3.6-27b",
        "pipeline":     "CMSCompOpsAgent (full live tools)",
        "tool_set":     "live",
        "out_file":     "config_qwen-27b_live.json",
    },
    "qwen3.6-27b/no-tools": {
        "model":        "qwen/qwen3.6-27b",
        "pipeline":     "CMSCompOpsAgent (no live tools)",
        "tool_set":     "no-tools",
        "out_file":     "config_qwen-27b_no-tools.json",
    },
    "qwen3.6-27b/rag": {
        "model":        "qwen/qwen3.6-27b",
        "pipeline":     "QAPipeline (single-shot RAG)",
        "tool_set":     "rag",
        "out_file":     "config_qwen-27b_rag.json",
    },
    "qwen3.6-35b/live": {
        "model":        "qwen/qwen3.6-35b-a3b",
        "pipeline":     "CMSCompOpsAgent (full live tools)",
        "tool_set":     "live",
        "out_file":     "config_qwen-35b_live.json",
    },
    "qwen3.6-27b/bare-llm": {
        "model":        "qwen/qwen3.6-27b",
        "pipeline":     "BareLLMPipeline (no tools, no system prompt)",
        "tool_set":     "bare-llm",
        "out_file":     "config_qwen-27b_bare-llm.json",
    },
}


def select_tools(tool_set, rucio_tools, monit_tools, catalog_tools,
                 read_skill_tool, vectorstore_tool):
    """Pick the right tool subset for each config tag."""
    if tool_set == "live":
        return rucio_tools + monit_tools + catalog_tools + [vectorstore_tool, read_skill_tool]
    if tool_set == "no-tools":
        # No live MONIT, no Rucio MCP. Keep catalog + vectorstore + read_skill.
        return catalog_tools + [vectorstore_tool, read_skill_tool]
    if tool_set == "rag":
        # Single tool: vectorstore_hybrid only. ReAct will run it once.
        return [vectorstore_tool]
    if tool_set == "bare-llm":
        # No tools at all — answered by a direct llm.invoke() in run_config.
        return []
    raise ValueError(f"unknown tool_set: {tool_set}")


async def run_config(slug, cfg, questions, tools_by_set, system_prompt):
    """Run one config across all 10 questions and save its result file."""
    out_file = OUT_DIR / cfg["out_file"]
    print(f"\n{'='*70}")
    print(f"  CONFIG: {slug}")
    print(f"  model:        {cfg['model']}")
    print(f"  pipeline:     {cfg['pipeline']}")
    print(f"  tool_set:     {cfg['tool_set']}")
    print(f"  out_file:     {out_file.relative_to(REPO)}")
    print(f"{'='*70}")

    tools = tools_by_set[cfg["tool_set"]]
    print(f"  tools bound: {len(tools)} ({', '.join(t.name for t in tools[:5])}{'…' if len(tools) > 5 else ''})")

    def make_agent():
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        client = httpx.AsyncClient(timeout=timeout)
        # Per-model provider pin. Avoids Chutes (67% uptime → Stage A
        # paralysis on qwen-27b) and Parasail/AkashML (tool-schema rejecters
        # for qwen-35b from earlier runs). Alibaba doesn't serve qwen-35b,
        # so 35b uses DekaLLM (100% uptime, similar untried-and-clean profile).
        # Multi-provider allowlist (in order). allow_fallbacks=False means
        # OpenRouter walks this list, picks first available — no silent
        # routing to AkashML / Parasail / Chutes (known bad for our schema).
        provider_pin = {
            "qwen/qwen3.6-27b":     ["Alibaba", "Ambient", "Io Net", "WandB"],
            # qwen-35b: Ambient advertises tools but breaks on bind_tools
            # (empty content + empty tool_calls). Parasail/AkashML emit
            # tool_calls correctly; rare schema rejection on 45-tool surface
            # is loud and recoverable, better than Ambient's silent failure.
            "qwen/qwen3.6-35b-a3b": ["Parasail", "AkashML"],
        }.get(cfg["model"])
        extra_body = {}
        if provider_pin:
            extra_body["provider"] = {"order": provider_pin, "allow_fallbacks": False}
        # qwen-35b on Ambient enters extended-reasoning mode that burns all
        # output tokens on internal CoT and emits no tool calls. Disable it
        # so the model commits to tool use instead.
        if cfg["model"] == "qwen/qwen3.6-35b-a3b":
            extra_body["reasoning"] = {"enabled": False}
        llm = ChatOpenAI(
            model=cfg["model"],
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
            temperature=0,
            timeout=90,
            max_retries=1,
            http_async_client=client,
            extra_body=extra_body,
        )
        loaded_skills: set = set()
        wrapped = smoke.wrap_tools_with_auto_skill_inject(tools, loaded_skills)
        return create_react_agent(llm, wrapped, prompt=system_prompt)

    # If the output file already exists (e.g. retry-only mode), seed
    # single_q_results from it so we only overwrite the questions we run.
    single_q_results = {}
    if out_file.exists():
        try:
            prior = json.loads(out_file.read_text())
            single_q_results = prior["benchmarking_results"][0]["single_question_results"]
            print(f"  seeded {len(single_q_results)} existing results from {out_file.name}")
        except Exception:
            single_q_results = {}

    # Bare-LLM short-circuit: single llm.invoke per question, no agent loop,
    # no system prompt, no tool catalog. Matches BareLLMPipeline behavior.
    bare_llm = cfg["tool_set"] == "bare-llm"

    def make_bare_llm():
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        client = httpx.AsyncClient(timeout=timeout)
        provider_pin = {
            "qwen/qwen3.6-27b":     ["Alibaba", "Ambient", "Io Net", "WandB"],
            # qwen-35b: Ambient advertises tools but breaks on bind_tools
            # (empty content + empty tool_calls). Parasail/AkashML emit
            # tool_calls correctly; rare schema rejection on 45-tool surface
            # is loud and recoverable, better than Ambient's silent failure.
            "qwen/qwen3.6-35b-a3b": ["Parasail", "AkashML"],
        }.get(cfg["model"])
        extra_body = {}
        if provider_pin:
            extra_body["provider"] = {"order": provider_pin, "allow_fallbacks": False}
        # qwen-35b on Ambient enters extended-reasoning mode that burns all
        # output tokens on internal CoT and emits no tool calls. Disable it
        # so the model commits to tool use instead.
        if cfg["model"] == "qwen/qwen3.6-35b-a3b":
            extra_body["reasoning"] = {"enabled": False}
        return ChatOpenAI(
            model=cfg["model"],
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
            temperature=0,
            timeout=90,
            max_retries=1,
            http_async_client=client,
            extra_body=extra_body,
        )

    for q in questions:
        qid = f"question_{q['idx']}"
        qtext = q["question"]
        print(f"\n--- {qid}: {qtext[:80]}{'...' if len(qtext) > 80 else ''}")

        if bare_llm:
            llm = make_bare_llm()
            t0 = time.time()
            try:
                resp = await llm.ainvoke(qtext)
                result = {
                    "question": qtext,
                    "answer": resp.content,
                    "time_elapsed": time.time() - t0,
                    "trace_events": [],
                    "sources_metadata": [],
                    "model_used": cfg["model"],
                    "pipeline_used": cfg["pipeline"],
                    "error": None,
                }
            except Exception as e:
                result = {
                    "question": qtext,
                    "answer": "",
                    "time_elapsed": time.time() - t0,
                    "trace_events": [],
                    "sources_metadata": [],
                    "model_used": cfg["model"],
                    "pipeline_used": cfg["pipeline"],
                    "error": f"{type(e).__name__}: {e}",
                }
        else:
            agent = make_agent()
            result = await smoke.run_one_question(agent, qid, qtext)
        result["model_used"] = cfg["model"]
        result["pipeline_used"] = cfg["pipeline"]
        n_tools = sum(1 for ev in result["trace_events"] if ev["type"] == "tool_call")
        ans_len = len(result.get("answer", ""))
        err_short = (result.get("error") or "")[:80]
        if err_short:
            print(f"    time={result['time_elapsed']:.1f}s calls={n_tools}  ERROR: {err_short}")
        else:
            print(f"    time={result['time_elapsed']:.1f}s calls={n_tools}  ans={ans_len} ch")
        single_q_results[qid] = result
        out = {"benchmarking_results": [{
            "single_question_results": single_q_results,
            "eval_name": cfg["out_file"].replace(".json", ""),
        }]}
        out_file.write_text(json.dumps(out, indent=2, default=str))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--configs", nargs="+", required=True,
                        help="Config slugs to run (e.g. gpt-5.5/live qwen3.6-27b/live)")
    parser.add_argument("--questions", nargs="+", type=int, default=None,
                        help="Optional idx values to restrict to (e.g. 3 5 6). "
                             "Results merged into the existing config output file.")
    args = parser.parse_args()

    for c in args.configs:
        if c not in CONFIGS:
            print(f"ERROR: unknown config {c!r}. Choices: {list(CONFIGS.keys())}")
            sys.exit(2)

    print("=== loading shared tools (rucio MCP + MONIT + catalog + read_skill + vectorstore) ===")
    rucio_tools = await smoke.collect_rucio_tools()
    monit_tools = smoke.build_monit_tools()
    catalog_tools = smoke.build_catalog_tools()
    read_skill_tool = smoke.build_read_skill_tool()

    # Build the vectorstore_hybrid tool using the same RemoteCatalogClient
    # the catalog tools use (data-manager on submit75:7871 via tunnel)
    from importlib import import_module
    # smoke.RemoteCatalogClient is the class; we need an instance. The catalog
    # tools were built from an instance, but it's not exposed. Build a fresh one.
    catalog_client = smoke.RemoteCatalogClient(base_url="http://127.0.0.1:7871", timeout=60.0)
    vectorstore_tool = vs_mod.make_search_vectorstore_hybrid(catalog_client)
    print(f"  rucio (MCP): {len(rucio_tools)}")
    print(f"  MONIT:       {len(monit_tools)}")
    print(f"  catalog:     {len(catalog_tools)}")
    print(f"  vectorstore: 1 (search_vectorstore_hybrid via data-manager hybrid mode)")
    print(f"  read_skill:  1")

    tools_by_set = {
        "live":     select_tools("live",     rucio_tools, monit_tools, catalog_tools,
                                 read_skill_tool, vectorstore_tool),
        "no-tools": select_tools("no-tools", rucio_tools, monit_tools, catalog_tools,
                                 read_skill_tool, vectorstore_tool),
        "rag":      select_tools("rag",      rucio_tools, monit_tools, catalog_tools,
                                 read_skill_tool, vectorstore_tool),
        "bare-llm": [],
    }
    for k, v in tools_by_set.items():
        print(f"  tool-set '{k}': {len(v)} tools — {', '.join(t.name for t in v[:6])}{'…' if len(v) > 6 else ''}")

    system_prompt = smoke.load_system_prompt()
    print(f"\n  system prompt: {len(system_prompt)} ch")

    questions = json.loads((REPO / "configs/submit75/grading_questions_tool_use_q10.json").read_text())
    if args.questions:
        wanted = set(args.questions)
        questions = [q for q in questions if q["idx"] in wanted]
        print(f"  questions:     {len(questions)} (filtered to idx {sorted(wanted)})")
    else:
        print(f"  questions:     {len(questions)}")

    for slug in args.configs:
        await run_config(slug, CONFIGS[slug], questions, tools_by_set, system_prompt)

    print(f"\n=== done. {len(args.configs)} config(s) written to {OUT_DIR.relative_to(REPO)} ===")


if __name__ == "__main__":
    asyncio.run(main())
