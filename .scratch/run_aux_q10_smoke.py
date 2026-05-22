"""Minimal direct benchmark: run the 10 tool-use questions with
qwen3.6-35b (OpenRouter) + Rucio MCP tools (via SSH tunnel to submit75).

This bypasses `archi evaluate` and the podman-deployment flow. It's a
Proposal-1-verification smoke, not a paper-grade run. Output schema
matches what gather_paper_data_split.py expects:

  bench_out/aux_q10_smoke/<config>.json
    {benchmarking_results: [{single_question_results: {q1: {...}, ...}}]}

Each q has: question, answer, time_elapsed, model_used, pipeline_used,
trace_events, sources_metadata. trace_events records every tool call so
we can audit whether rucio_* was actually called.

Requires SSH tunnel: ssh -N -L 8000:127.0.0.1:8000 mohoney@submit75.mit.edu
(run_aux_q10_smoke.sh sets this up).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

REPO = Path("/Users/jason/projects/A2rchi")
load_dotenv(REPO / ".env")

QUESTIONS = REPO / "configs/submit75/grading_questions_tool_use_q10.json"
AGENT_SPEC = REPO / "examples/agents/cms-comp-ops.md"
RUCIO_MCP_SKILL = REPO / "examples/skills/rucio_mcp.md"
RUCIO_EVENTS_SKILL = REPO / "examples/skills/rucio_events.md"
CONDOR_METRIC_SKILL = REPO / "examples/skills/condor_raw_metric.md"
OUT_DIR = REPO / "bench_out/aux_q10_smoke"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "qwen-27b-thinking-off.json"

# Direct file-level import of the MONIT helpers — bypasses the package __init__
# which drags in langchain_classic (not available locally).
_monit_spec = importlib.util.spec_from_file_location(
    "_monit_module",
    str(REPO / "src/archi/pipelines/agents/tools/monit_opensearch.py"),
)
_monit_mod = importlib.util.module_from_spec(_monit_spec)
# Provide a minimal logging shim before exec — the file imports src.utils.logging
class _StubLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass
    def debug(self, *a, **k): pass
sys.modules.setdefault("src", type(sys)("src"))
sys.modules.setdefault("src.utils", type(sys)("src.utils"))
_utils_logging = type(sys)("src.utils.logging")
_utils_logging.get_logger = lambda name="_": _StubLogger()
sys.modules["src.utils.logging"] = _utils_logging
# src.utils.env shim — read_secret returns None (no DM_API_TOKEN locally;
# the data-manager on submit75 is unauthenticated within its container netns).
_utils_env = type(sys)("src.utils.env")
_utils_env.read_secret = lambda *a, **k: None
sys.modules["src.utils.env"] = _utils_env
# src.archi.pipelines.agents.tools.base stub — require_tool_permission is a
# pass-through decorator; the real one fails-open in non-Flask contexts anyway.
sys.modules.setdefault("src.archi", type(sys)("src.archi"))
sys.modules.setdefault("src.archi.pipelines", type(sys)("src.archi.pipelines"))
sys.modules.setdefault("src.archi.pipelines.agents", type(sys)("src.archi.pipelines.agents"))
sys.modules.setdefault("src.archi.pipelines.agents.tools", type(sys)("src.archi.pipelines.agents.tools"))
_base_mod = type(sys)("src.archi.pipelines.agents.tools.base")
def _passthrough_decorator(_perm):
    def deco(fn): return fn
    return deco
_base_mod.require_tool_permission = _passthrough_decorator
sys.modules["src.archi.pipelines.agents.tools.base"] = _base_mod

_monit_spec.loader.exec_module(_monit_mod)
MONITOpenSearchClient = _monit_mod.MONITOpenSearchClient
create_monit_opensearch_search_tool = _monit_mod.create_monit_opensearch_search_tool
create_monit_opensearch_aggregation_tool = _monit_mod.create_monit_opensearch_aggregation_tool
create_monit_fetch_document_tool = _monit_mod.create_monit_fetch_document_tool

# Direct-load local_files for grep + catalog/metadata tools
_lf_spec = importlib.util.spec_from_file_location(
    "_local_files_module",
    str(REPO / "src/archi/pipelines/agents/tools/local_files.py"),
)
_lf_mod = importlib.util.module_from_spec(_lf_spec)
_lf_spec.loader.exec_module(_lf_mod)
RemoteCatalogClient = _lf_mod.RemoteCatalogClient
create_grep_tool = _lf_mod.create_grep_tool
create_metadata_search_tool = _lf_mod.create_metadata_search_tool
create_metadata_schema_tool = _lf_mod.create_metadata_schema_tool
create_document_fetch_tool = _lf_mod.create_document_fetch_tool

# Per-question hard timeout. The agent may loop endlessly on tools — cap it.
PER_QUESTION_TIMEOUT_S = 300
MAX_TOOL_ITERATIONS = 30


SKILL_REGISTRY = {
    "rucio_mcp": {
        "path": RUCIO_MCP_SKILL,
        "trigger": (
            "READ-ONLY contract + reproducibility convention for all `rucio_*` "
            "MCP tools. Load BEFORE any non-trivial Rucio query (e.g. before "
            "list_dataset_replicas, get_rse_usage, list_did_rules, etc.). "
            "Without it you may forget the read-only contract and the "
            "<details>-block reproducibility convention."
        ),
    },
    "rucio_events": {
        "path": RUCIO_EVENTS_SKILL,
        "trigger": (
            "Field reference + Lucene query patterns for the "
            "`monit_prod_cms_rucio_raw_events*` index. Load BEFORE the first "
            "call to `rucio_events_search`, `rucio_events_aggregation`, or "
            "`fetch_rucio_document`. Without it you'll guess wrong field "
            "names (e.g. `data.dst_rse` vs `data.dest_rse`) and Lucene "
            "queries will return zero hits."
        ),
    },
    "condor_raw_metric": {
        "path": CONDOR_METRIC_SKILL,
        "trigger": (
            "Field reference + Lucene query patterns for the "
            "`monit_prod_condor_raw_metric*` index. Load BEFORE the first "
            "call to `condor_metric_search`, `condor_metric_aggregation`, "
            "or `fetch_condor_document`. Without it you'll guess wrong "
            "field names (e.g. `data.CpuEff`, `data.RequestMemory`, "
            "`data.Workflow`, `data.GlobalJobId`) and queries will fail."
        ),
    },
}


def load_system_prompt() -> str:
    """Just the agent spec — no skill catalog. The `read_skill` tool's
    description contains the catalog so the model sees it at the point of
    tool selection (when it matters), not buried in a system-prompt table."""
    agent_text = AGENT_SPEC.read_text()
    if agent_text.startswith("---"):
        end = agent_text.find("---", 3)
        if end > 0:
            agent_text = agent_text[end + 3:].lstrip()
    return agent_text


def build_read_skill_tool():
    """A LangChain tool that returns the full text of a named skill.

    The tool's *description* contains the full skill catalog: each skill's
    name, what it covers, and when to load it. This matches the Anthropic
    Skills pattern — listing skills inside the discovery tool so the model
    sees them whenever it inspects its tool surface."""
    from langchain.tools import tool

    catalog_lines = []
    for name, meta in SKILL_REGISTRY.items():
        catalog_lines.append(f"- `{name}`: {meta['trigger']}")
    catalog_md = "\n".join(catalog_lines)

    description = (
        f"Return the full reference guide for a named skill. "
        f"Skills are lazy-loaded: each contains field references, query "
        f"patterns, and contracts the matching tools need.\n\n"
        f"AVAILABLE SKILLS — load the relevant one BEFORE the first call "
        f"to any matching tool:\n\n"
        f"{catalog_md}\n\n"
        f"On any non-trivial question that uses MONIT (rucio_events_* or "
        f"condor_metric_*) or non-trivial Rucio operations, your FIRST "
        f"tool call should be `read_skill` for the matching skill. Skipping "
        f"this leads to wrong field names, invalid RSE expressions, and "
        f"silent zero-hit queries."
    )

    @tool("read_skill", description=description)
    def _read_skill(name: str) -> str:
        meta = SKILL_REGISTRY.get(name)
        if meta is None:
            valid = ", ".join(sorted(SKILL_REGISTRY.keys()))
            return f"No skill named {name!r}. Valid: {valid}"
        return meta["path"].read_text()

    return _read_skill


# Mapping: tool name → required skill name. Tools not in this mapping that
# also start with `rucio_` are treated as Rucio MCP and need `rucio_mcp`.
SKILL_BY_TOOL = {
    "rucio_events_search":       "rucio_events",
    "rucio_events_aggregation":  "rucio_events",
    "fetch_rucio_document":      "rucio_events",
    "condor_metric_search":      "condor_raw_metric",
    "condor_metric_aggregation": "condor_raw_metric",
    "fetch_condor_document":     "condor_raw_metric",
}


def required_skill_for(tool_name: str):
    if tool_name in SKILL_BY_TOOL:
        return SKILL_BY_TOOL[tool_name]
    if tool_name.startswith("rucio_"):
        return "rucio_mcp"
    return None


def wrap_tools_with_auto_skill_inject(tools, loaded_skills: set):
    """Wrap each tool so its first invocation auto-prepends the skill text
    if read_skill hasn't been called for the matching skill yet. After the
    first auto-inject, the skill stays in conversation history; subsequent
    calls of any tool sharing that skill pass through cleanly.

    Also wraps `read_skill` so an explicit call marks the skill as loaded
    (no double-prepend on a later tool call).

    `loaded_skills` is a mutable set shared across all wrapped tools for
    a given question. Caller creates one per question."""
    from langchain_core.tools import StructuredTool

    def make_wrapper(orig, req_skill):
        skill_text = SKILL_REGISTRY[req_skill]["path"].read_text()
        preamble = (
            f"\n=== AUTO-LOADED SKILL: {req_skill} ===\n"
            f"This skill was auto-loaded because you called `{orig.name}` "
            f"without first invoking `read_skill('{req_skill}')`. Read this "
            f"guidance carefully — your call may have used incorrect field "
            f"names or argument formats; the result below may show a schema "
            f"error you can now fix on the next try.\n\n"
            f"{skill_text}\n"
            f"=== END SKILL ===\n\n"
            f"=== TOOL RESULT ===\n"
        )

        def _wrap_text(body: str) -> str:
            """Prepend the skill preamble on first call; pass through after."""
            if req_skill in loaded_skills:
                return body
            loaded_skills.add(req_skill)
            return f"{preamble}{body}"

        async def wrapped_coro(**kwargs):
            # Catch tool exceptions so the model sees the error AND the skill
            # text — without this, a bad-args call raises through the wrapper
            # and the skill never reaches the model, defeating the whole
            # point of auto-inject.
            try:
                result = await orig.ainvoke(kwargs)
                return _wrap_text(str(result))
            except Exception as e:
                err_body = f"ERROR calling {orig.name}: {type(e).__name__}: {e}"
                return _wrap_text(err_body)

        def wrapped_sync(**kwargs):
            try:
                result = orig.invoke(kwargs)
                return _wrap_text(str(result))
            except Exception as e:
                err_body = f"ERROR calling {orig.name}: {type(e).__name__}: {e}"
                return _wrap_text(err_body)

        return StructuredTool.from_function(
            func=wrapped_sync,
            coroutine=wrapped_coro,
            name=orig.name,
            description=orig.description,
            args_schema=orig.args_schema,
        )

    def wrap_read_skill(orig):
        async def wrapped_coro(**kwargs):
            name = kwargs.get("name")
            result = await orig.ainvoke(kwargs)
            if name in SKILL_REGISTRY:
                loaded_skills.add(name)
            return result

        def wrapped_sync(**kwargs):
            name = kwargs.get("name")
            result = orig.invoke(kwargs)
            if name in SKILL_REGISTRY:
                loaded_skills.add(name)
            return result

        return StructuredTool.from_function(
            func=wrapped_sync,
            coroutine=wrapped_coro,
            name=orig.name,
            description=orig.description,
            args_schema=orig.args_schema,
        )

    out = []
    for t in tools:
        if t.name == "read_skill":
            out.append(wrap_read_skill(t))
        else:
            req = required_skill_for(t.name)
            if req is None:
                out.append(t)
            else:
                out.append(make_wrapper(t, req))
    return out


async def collect_rucio_tools():
    cfg = {"rucio": {"transport": "streamable_http", "url": "http://127.0.0.1:8000/mcp"}}
    client = MultiServerMCPClient(cfg)
    tools = await client.get_tools(server_name="rucio")
    return tools


def build_monit_tools():
    """Mint the 6 MONIT tools the gold agent ships with. Uses the prod URLs
    from comp_ops_config.yaml + MONIT_GRAFANA_TOKEN from local .env."""
    token = os.environ.get("MONIT_GRAFANA_TOKEN")
    if not token:
        print("WARN: MONIT_GRAFANA_TOKEN missing — MONIT tools will not be wired", file=sys.stderr)
        return []

    # OPTION C: skills go into the system prompt as named sections (see
    # load_system_prompt), not into each tool's description. Pass skill=None
    # to the factories so they emit only the base tool description.
    monit_client = MONITOpenSearchClient(
        token=token,
        url="https://monit-grafana.cern.ch/api/datasources/proxy/9269/_msearch",
    )
    condor_client = MONITOpenSearchClient(
        token=token,
        url="https://monit-grafana.cern.ch/api/datasources/proxy/8787/_msearch",
    )

    rucio_idx = "monit_prod_cms_rucio_raw_events*"
    condor_idx = "monit_prod_condor_raw_metric*"

    return [
        create_monit_opensearch_search_tool(
            monit_client, tool_name="rucio_events_search",
            index=rucio_idx, skill=None),
        create_monit_opensearch_aggregation_tool(
            monit_client, tool_name="rucio_events_aggregation",
            index=rucio_idx, skill=None),
        create_monit_fetch_document_tool(
            monit_client, tool_name="fetch_rucio_document", index=rucio_idx),
        create_monit_opensearch_search_tool(
            condor_client, tool_name="condor_metric_search",
            index=condor_idx, skill=None),
        create_monit_opensearch_aggregation_tool(
            condor_client, tool_name="condor_metric_aggregation",
            index=condor_idx, skill=None),
        create_monit_fetch_document_tool(
            condor_client, tool_name="fetch_condor_document", index=condor_idx),
    ]


def build_catalog_tools():
    """Mint the 4 catalog tools (grep + metadata + fetch_catalog_document)
    against the data-manager catalog API tunneled from submit75:7871."""
    try:
        catalog = RemoteCatalogClient(base_url="http://127.0.0.1:7871", timeout=20.0)
        # Sanity check — fetch the catalog schema to confirm the API is reachable
        schema_probe = catalog.schema()
        n_indexed = schema_probe.get("counts", {}).get("documents")
        print(f"  catalog API reachable; {n_indexed} indexed docs" if n_indexed else
              "  catalog API reachable (no doc count in schema)")
    except Exception as e:
        print(f"WARN: catalog API not reachable ({type(e).__name__}: {e}); skipping catalog tools",
              file=sys.stderr)
        return []
    return [
        create_grep_tool(catalog, name="grep"),
        create_metadata_search_tool(catalog, name="search_metadata_index"),
        create_metadata_schema_tool(catalog, name="list_metadata_schema"),
        create_document_fetch_tool(catalog, description="Fetch the full text of a catalogued document by resource hash."),
    ]


async def run_one_question(agent, qid: str, qtext: str) -> dict:
    """Invoke the agent with the question. Capture answer + tool-call trace."""
    t0 = time.time()
    trace_events = []

    try:
        final_state = await asyncio.wait_for(
            agent.ainvoke(
                {"messages": [{"role": "user", "content": qtext}]},
                config={"recursion_limit": MAX_TOOL_ITERATIONS * 2},
            ),
            timeout=PER_QUESTION_TIMEOUT_S,
        )
        # Walk the message stream for tool calls + final answer
        messages = final_state.get("messages", [])
        final_answer = ""
        for m in messages:
            mtype = getattr(m, "type", None) or m.__class__.__name__
            if mtype in ("ai", "AIMessage"):
                # Capture tool calls
                tool_calls = getattr(m, "tool_calls", None) or []
                for tc in tool_calls:
                    trace_events.append({
                        "type": "tool_call",
                        "tool_name": tc.get("name", "?"),
                        "args": tc.get("args", {}),
                    })
                # If content non-empty, this could be the final synthesis
                content = getattr(m, "content", None) or ""
                if isinstance(content, str) and content.strip():
                    final_answer = content
            elif mtype in ("tool", "ToolMessage"):
                content = getattr(m, "content", None) or ""
                trace_events.append({
                    "type": "tool_output",
                    "tool_name": getattr(m, "name", "?"),
                    "output_preview": (str(content)[:500] + "...") if len(str(content)) > 500 else str(content),
                })
        result = {
            "question": qtext,
            "answer": final_answer,
            "time_elapsed": time.time() - t0,
            "model_used": "qwen/qwen3.6-27b",
            "pipeline_used": "ReActAgent (langgraph prebuilt)",
            "trace_events": trace_events,
            "sources_metadata": [],
            "error": None,
        }
    except asyncio.TimeoutError:
        result = {
            "question": qtext,
            "answer": "",
            "time_elapsed": time.time() - t0,
            "model_used": "qwen/qwen3.6-27b",
            "pipeline_used": "ReActAgent (langgraph prebuilt)",
            "trace_events": trace_events,
            "sources_metadata": [],
            "error": f"timeout after {PER_QUESTION_TIMEOUT_S}s",
        }
    except Exception as e:
        result = {
            "question": qtext,
            "answer": "",
            "time_elapsed": time.time() - t0,
            "model_used": "qwen/qwen3.6-27b",
            "pipeline_used": "ReActAgent (langgraph prebuilt)",
            "trace_events": trace_events,
            "sources_metadata": [],
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc(),
        }
    return result


async def main():
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("FATAL: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(2)

    print("=== Loading agent + tools ===")
    system_prompt = load_system_prompt()
    print(f"  system prompt: {len(system_prompt)} chars "
          f"(agent + 3 skills: rucio_mcp, rucio_events, condor_raw_metric)")

    rucio_tools = await collect_rucio_tools()
    print(f"  rucio_* MCP tools advertised: {len(rucio_tools)}")
    if not rucio_tools:
        print("FATAL: no tools from MCP — is the tunnel up?", file=sys.stderr)
        sys.exit(3)

    monit_tools = build_monit_tools()
    print(f"  MONIT tools wired: {len(monit_tools)}")
    for t in monit_tools:
        print(f"    {t.name}")

    catalog_tools = build_catalog_tools()
    print(f"  catalog tools wired: {len(catalog_tools)}")
    for t in catalog_tools:
        print(f"    {t.name}")

    read_skill_tool = build_read_skill_tool()
    print(f"  utility tools wired: 1 ({read_skill_tool.name})")

    tools = rucio_tools + monit_tools + catalog_tools + [read_skill_tool]
    print(f"  TOTAL tools bound: {len(tools)}")

    # qwen/qwen3.6-27b via OpenRouter — matches the gold tier's
    # qwen3.6-27b/live config. 7 providers (DeepInfra, Io Net, Venice,
    # Alibaba, Morph, Chutes, WandB) — no AkashML/Parasail here.
    #
    # Per-question we build a FRESH client + agent so a wedged httpx
    # connection on one question doesn't cascade APIConnectionError on
    # the next.  httpx.Timeout is set chunk-level so a streaming
    # provider that emits one byte per minute still dies cleanly.
    import httpx
    def make_agent():
        # Bounded chunk-level timeout so a slow-streaming provider can't hang
        # for >60s on read. agent.ainvoke uses the ASYNC client.
        timeout = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
        async_http_client = httpx.AsyncClient(timeout=timeout)
        llm = ChatOpenAI(
            model="qwen/qwen3.6-27b",
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url="https://openrouter.ai/api/v1",
            temperature=0,
            timeout=90,
            max_retries=1,
            http_async_client=async_http_client,
            # Disable thinking mode — Q3/Q6 paralysis appears to be the model
            # entering extended reasoning that never resolves to a tool call.
            extra_body={"reasoning": {"enabled": False}},
        )
        # Per-question loaded_skills set — when the model calls a skill-aware
        # tool without read_skill first, the wrapper auto-injects the skill
        # text into the result. Subsequent calls in the same question see
        # the skill already loaded.
        loaded_skills: set = set()
        wrapped_tools = wrap_tools_with_auto_skill_inject(tools, loaded_skills)
        return create_react_agent(llm, wrapped_tools, prompt=system_prompt)

    agent = make_agent()

    questions = json.loads(QUESTIONS.read_text())
    print(f"  questions: {len(questions)} loaded")

    single_q_results = {}
    for q in questions:
        qid = f"question_{q['idx']}"
        qtext = q["question"]
        print(f"\n--- {qid}: {qtext[:80]}{'...' if len(qtext) > 80 else ''}")
        # Fresh agent per question — avoids stale-httpx cascade if one
        # question's connection wedges.
        agent = make_agent()
        result = await run_one_question(agent, qid, qtext)
        rucio_calls = sum(1 for ev in result["trace_events"]
                          if ev["type"] == "tool_call" and ev["tool_name"].startswith("rucio_"))
        total_calls = sum(1 for ev in result["trace_events"] if ev["type"] == "tool_call")
        ans_preview = (result["answer"][:200] + "…") if len(result["answer"]) > 200 else result["answer"]
        print(f"    time={result['time_elapsed']:.1f}s tool_calls={total_calls} rucio_calls={rucio_calls}")
        if result.get("error"):
            print(f"    ERROR: {result['error']}")
        else:
            print(f"    answer_preview: {ans_preview!r}")
        single_q_results[qid] = result

        # Incremental save so a crash doesn't lose progress
        out = {"benchmarking_results": [{"single_question_results": single_q_results}]}
        OUT_FILE.write_text(json.dumps(out, indent=2, default=str))

    print(f"\n=== complete; results at {OUT_FILE} ===")


if __name__ == "__main__":
    asyncio.run(main())
