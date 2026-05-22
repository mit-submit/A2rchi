"""Driver for `rag` and `bare` configs — invokes the REAL pipelines.

  rag   → src.archi.pipelines.QAPipeline (condense + HybridRetriever + stuff_documents)
  bare  → src.archi.pipelines.BareLLMPipeline (single chat_model.invoke)

We construct a minimal config dict that wires both pipelines' LLMs at our
vLLM endpoint via LocalProvider (local_mode=openai_compat). Prompts come
from the repo's examples/defaults/prompts/ via the BasePipeline
prompt_overrides hook.

For RAG: we use the same Postgres-free HTTP retrieval path the gold-tier
benchmark uses (data-manager /api/catalog/search?mode=hybrid) but wired
behind a vectorstore wrapper so HybridRetriever doesn't know the
difference. That keeps QAPipeline.invoke literally untouched.

Same output format as run_260q_orcd_v3.py so the dashboard / analysis
scripts work uniformly. Same idempotent resume.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import List, Optional

REPO = Path(os.environ.get("ORCD_REPO", os.path.expanduser("~/A2rchi")))
SECRETS_DIR = Path(os.environ.get("ARCHI_SECRETS_DIR",
                                  os.path.expanduser("~/.archi-bundle-state/bundle/secrets/archi")))
DEFAULT_QUESTIONS = REPO / "configs/submit75/curated_questions.json"
OUT_DIR = Path(os.environ.get("ORCD_OUT_DIR", os.path.expanduser("~/bench_out/run_260q_orcd_v3")))
OUT_DIR.mkdir(parents=True, exist_ok=True)

ARCHI_DM_URL = os.environ.get("ARCHI_DM_URL")
VLLM_URL     = os.environ.get("VLLM_URL")
VLLM_MODEL   = os.environ.get("VLLM_MODEL")

PER_QUESTION_TIMEOUT_S = int(os.environ.get("PER_QUESTION_TIMEOUT_S", "600"))
SEED                   = int(os.environ.get("VLLM_SEED", "42"))
RAG_K                  = int(os.environ.get("RAG_K", "15"))
OUTPUT_PREVIEW_CHARS   = int(os.environ.get("OUTPUT_PREVIEW_CHARS", "2000"))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

for name in ("openai_api_key", "openrouter_api_key", "monit_grafana_token",
             "jira_pat", "dm_api_token"):
    f = SECRETS_DIR / f"{name}.txt"
    if f.exists():
        os.environ[name.upper()] = f.read_text().strip()

# Make the repo importable so we can `from src.archi.pipelines import ...`
sys.path.insert(0, str(REPO))

# Some Archi modules expect a logger shim if src.utils.logging isn't fully
# initialized in this leaner container. Try the real one first.
try:
    from src.utils.logging import get_logger  # noqa: F401
except Exception:
    class _StubLogger:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def error(self, *a, **k): pass
        def debug(self, *a, **k): pass
    import types as _t
    sys.modules.setdefault("src", _t.ModuleType("src"))
    sys.modules.setdefault("src.utils", _t.ModuleType("src.utils"))
    _mod = _t.ModuleType("src.utils.logging")
    _mod.get_logger = lambda name="_": _StubLogger()
    sys.modules["src.utils.logging"] = _mod

# The chain_wrappers used by QAPipeline assigns `global_configs = get_global_config()`
# in _prepare_payload but never uses the result — it's just dead code. The
# function itself goes through PostgresServiceFactory which we don't have set
# up (no live Postgres connection from this bench node). Monkey-patch the
# config-access functions to return empty dicts BEFORE the pipeline imports
# bind them at import time.
import src.utils.config_access as _config_access
_config_access.get_global_config       = lambda: {}
_config_access.get_full_config         = lambda **kw: {}
_config_access.get_data_manager_config = lambda **kw: {}
_config_access.get_archi_config        = lambda: {}
_config_access.get_services_config     = lambda: {}
_config_access.get_static_config       = lambda: type("S", (), {
    "global_config": {}, "services_config": {}, "data_manager_config": {},
    "archi_config": {}, "sources_config": {}, "mcp_servers_config": {},
    "available_pipelines": {}, "available_models": {}, "available_providers": {},
    "deployment_name": "bench", "config_version": "0",
})()

# Import the real pipelines (after the monkey-patch above)
from src.archi.pipelines import QAPipeline, BareLLMPipeline


# ------------ vectorstore wrapper for QAPipeline's HybridRetriever ------------

from langchain_core.vectorstores.base import VectorStore as _VectorStore
from langchain_core.documents import Document as _Document


_STOPWORDS = set("""
a about an and any are as at be been being but by can could did do does for
from had has have he her here him his how i if in into is it its just like me
my no not of on or our should so some that the their them then there these they
this those to was we were what when where which who whom why will with would you
your yours i'm it's that's what's
""".split())


class HTTPHybridVectorstore(_VectorStore):
    """LangChain VectorStore that proxies retrieval to the data-manager's
    /api/catalog/search.

    The deployed data-manager has only two real modes — `grep` (ripgrep) and
    a default substring-match. There is no hybrid/semantic endpoint exposed
    over HTTP. We approximate BM25 by extracting content keywords from the
    query and ripgrep'ing for any of them with a regex alternation. Returns
    up to k documents (snippets).

    HybridRetriever calls hybrid_search() if available. We implement that
    plus the abstract methods (similarity_search, add_texts, from_texts).
    """

    def __init__(self, base_url: str, timeout: float = 120.0, **kwargs):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @staticmethod
    def _extract_keywords(query: str, *, max_terms: int = 12) -> list:
        import re
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", query)
        seen = set()
        kws = []
        for t in tokens:
            lt = t.lower()
            if lt in _STOPWORDS or lt in seen:
                continue
            seen.add(lt)
            kws.append(t)
            if len(kws) >= max_terms:
                break
        return kws

    def _ripgrep_keywords(self, query: str, k: int) -> list:
        """Call mode=grep with regex=\\b(kw1|kw2|...)\\b case-insensitive."""
        import re, requests
        kws = self._extract_keywords(query)
        if not kws:
            return []
        # The data-manager's grep regex engine rejects both \b word boundaries
        # and (?:...) non-capturing groups (each returns 0 hits silently).
        # Plain alternation works.
        pattern = "|".join(re.escape(w) for w in kws)
        resp = requests.get(
            f"{self.base_url}/api/catalog/search",
            params={
                "q": pattern,
                "limit": k,
                "mode": "grep",
                "regex": "true",
                "case_sensitive": "false",
                "search_content": "true",
                "before": 0,
                "after": 0,
                "max_matches_per_file": 3,
            },
            timeout=self.timeout,
            allow_redirects=False,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", []) or []
        out = []
        for h in hits:
            text = h.get("snippet") or ""
            if not text and h.get("matches"):
                # Concatenate up to 3 match lines for better context.
                text = "\n".join(m.get("text", "") for m in h["matches"][:3])
            md = h.get("metadata") or {}
            md.setdefault("source", h.get("path", ""))
            score = float(len(h.get("matches", [])) or 1.0)  # match count as crude score
            out.append((_Document(page_content=text, metadata=md), score))
        return out

    # HybridRetriever's primary call
    def hybrid_search(self, query: str, k: int = 5,
                      semantic_weight: float = 0.5, bm25_weight: float = 0.5, **kwargs):
        return self._ripgrep_keywords(query, k)

    # Fallback path
    def similarity_search_with_score(self, query: str, k: int = 5, **kwargs):
        return self._ripgrep_keywords(query, k)

    # Required abstract methods
    def similarity_search(self, query: str, k: int = 5, **kwargs):
        return [doc for doc, _ in self._ripgrep_keywords(query, k)]

    def add_texts(self, texts, metadatas=None, **kwargs):
        raise NotImplementedError("HTTPHybridVectorstore is read-only")

    @classmethod
    def from_texts(cls, texts, embedding, metadatas=None, **kwargs):
        raise NotImplementedError("HTTPHybridVectorstore is read-only")


# ------------ config builder ------------

def build_archi_config(
    tool_set: str,
    *,
    llm_provider: str,
    model_name: str,
    base_url: Optional[str],
    use_responses_api: bool,
    reasoning_effort: Optional[str],
) -> dict:
    """Minimum config dict BasePipeline needs to wire the selected LLM.

    Path: BasePipeline._init_llms reads
      config["archi"]["pipeline_map"][<ClassName>]["models"]["required" | "optional"]
    Each entry is "provider/model_id". For vLLM we use "local" → LocalProvider
    with openai_compat; for GPT-5.5 we use the OpenAI provider directly.
    """
    provider_key = "local" if llm_provider == "vllm" else llm_provider
    model_ref = f"{provider_key}/{model_name}"

    if llm_provider == "vllm":
        provider_options = {
            "local_mode": "openai_compat",
            "temperature": 0,
            # vLLM honors seed via OpenAI chat completions extra
            "extra_body": {"chat_template_kwargs": {"enable_thinking": False}, "seed": SEED},
            "max_retries": 1,
            # request timeout (seconds)
            "timeout": 200,
        }
        providers = {
            "local": {
                "base_url": base_url,
                "mode": "openai_compat",
                "options": provider_options,
            }
        }
    else:
        provider_options = {
            "temperature": 0,
            "max_retries": 1,
            "timeout": 200,
        }
        if llm_provider == "openai":
            provider_options["use_responses_api"] = use_responses_api
            if reasoning_effort:
                provider_options["reasoning_effort"] = reasoning_effort
        providers = {
            llm_provider: {
                "base_url": base_url,
                "default_model": model_name,
                "models": [model_name],
                "options": provider_options,
            }
        }

    common_pipeline_map = {
        "BareLLMPipeline": {
            "models": {
                "required": {"chat_model": model_ref},
            },
        },
        "QAPipeline": {
            "models": {
                "required": {
                    "chat_model":    model_ref,
                    "condense_model": model_ref,
                },
            },
            "max_tokens": 7000,
        },
    }
    return {
        "archi": {"pipeline_map": common_pipeline_map},
        "data_manager": {
            "retrievers": {
                "hybrid_retriever": {
                    "num_documents_to_retrieve": RAG_K,
                    "bm25_weight": 0.6,
                    "semantic_weight": 0.4,
                }
            }
        },
        "services": {
            "chat_app": {
                "providers": providers
            }
        },
    }


# ------------ pipeline constructors ------------

def make_pipeline(tool_set: str, llm_settings: dict):
    config = build_archi_config(tool_set, **llm_settings)
    prompt_overrides = {
        "condense_prompt": str(REPO / "examples/defaults/prompts/condense/default.prompt"),
        "chat_prompt":     str(REPO / "examples/defaults/prompts/chat/default.prompt"),
    }
    if tool_set == "rag":
        return QAPipeline(config=config, prompt_overrides=prompt_overrides)
    if tool_set == "bare":
        return BareLLMPipeline(config=config)
    raise ValueError(f"unknown tool_set: {tool_set!r}")


# ------------ per-question driver ------------

def _now() -> int: return int(time.time())

def _event(d: dict) -> dict:
    d.setdefault("ts_epoch", _now())
    return d


def _result_skeleton(qid, qtext, pipeline_name, t0, *, answer="", error=None, tb=None,
                     model_name=None):
    out = {
        "question": qtext,
        "answer": answer,
        "time_elapsed": time.time() - t0,
        "model_used": model_name or VLLM_MODEL or "?",
        "pipeline_used": pipeline_name,
        "trace_events": [],
        "sources_metadata": [],
        "error": error,
        "completed_ts_epoch": _now(),
    }
    if tb: out["traceback"] = tb
    return out


def _convert_pipeline_output(qid, qtext, po, t0, pipeline_name, model_name):
    """PipelineOutput → v3-format result dict + synthetic trace events."""
    meta = getattr(po, "metadata", {}) or {}
    result = _result_skeleton(
        qid, qtext, pipeline_name, t0,
        model_name=meta.get("model_used") or model_name,
    )
    answer = getattr(po, "answer", "") or ""
    result["answer"] = answer
    src_docs = getattr(po, "source_documents", None) or []
    scores = meta.get("retriever_scores") or []

    # rag_retrieve event — always emitted for QA pipeline so we can see what
    # the condense step produced and how many docs the retriever returned.
    if pipeline_name == "QAPipeline":
        result["trace_events"].append(_event({
            "type": "rag_retrieve",
            "n_hits": len(src_docs),
            "scores": [float(s) for s in scores],
            "condensed_output": (meta.get("condensed_output") or "")[:OUTPUT_PREVIEW_CHARS],
            "original_question": qtext[:200],
            "doc_sources": [str((d.metadata or {}).get("source", "")) for d in src_docs],
            "doc_chars": [len(getattr(d, "page_content", "") or "") for d in src_docs],
        }))
        result["sources_metadata"] = [
            {
                "path": str((d.metadata or {}).get("source", "")),
                "score": float(scores[i]) if i < len(scores) else None,
                "chars": len(getattr(d, "page_content", "") or ""),
            }
            for i, d in enumerate(src_docs)
        ]

    usage = meta.get("usage") or {}
    thinking = meta.get("thinking_content") or ""
    # Aggregated llm_call event (QAPipeline returns aggregated usage covering
    # condense + answer; BareLLMPipeline is one call).
    result["trace_events"].append(_event({
        "type": "llm_call",
        "iter": 1,
        "model": result["model_used"],
        "duration_s": time.time() - t0,  # whole-pipeline duration; per-call not exposed
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "content_chars": len(answer),
        "thinking_chars": len(thinking),
        "with_tools": False,
    }))
    return result


async def run_one(qid, qtext, pipeline, vectorstore, *, pipeline_name, model_name):
    t0 = time.time()
    try:
        def _invoke_sync():
            kwargs = {"history": [("User", qtext)]}
            if vectorstore is not None:
                kwargs["vectorstore"] = vectorstore
            return pipeline.invoke(**kwargs)

        po = await asyncio.wait_for(asyncio.to_thread(_invoke_sync), timeout=PER_QUESTION_TIMEOUT_S)
        return _convert_pipeline_output(qid, qtext, po, t0, pipeline_name, model_name)
    except asyncio.TimeoutError:
        return _result_skeleton(qid, qtext, pipeline_name, t0,
                                error=f"per-question timeout {PER_QUESTION_TIMEOUT_S}s",
                                model_name=model_name)
    except Exception as e:
        return _result_skeleton(qid, qtext, pipeline_name, t0,
                                error=f"{type(e).__name__}: {e}",
                                tb=traceback.format_exc(), model_name=model_name)


# ------------ main ------------

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--questions", type=str, default=str(DEFAULT_QUESTIONS),
                        help="Question JSON file. Defaults to the canonical 260-question set.")
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument("--tool-set", choices=["rag", "bare"], required=True)
    parser.add_argument("--concurrency", type=int,
                        default=int(os.environ.get("CONCURRENCY_OVERRIDE", "32")))
    parser.add_argument("--retry-errored", action="store_true")
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
    args = parser.parse_args()

    llm_provider = args.llm_provider.lower()
    if llm_provider == "vllm":
        model_name = args.model or VLLM_MODEL or sys.exit("VLLM_MODEL required for --llm-provider vllm")
        base_url = args.base_url or VLLM_URL or sys.exit("VLLM_URL required for --llm-provider vllm")
        api_key_env = None
    elif llm_provider == "openai":
        model_name = args.model or os.environ.get("OPENAI_MODEL") or "gpt-5.5-2026-04-23"
        base_url = args.base_url
        api_key_env = args.api_key_env or "OPENAI_API_KEY"
        os.environ.get(api_key_env) or sys.exit(f"{api_key_env} required for --llm-provider openai")
    elif llm_provider == "openrouter":
        model_name = args.model or os.environ.get("OPENROUTER_MODEL") or "openai/gpt-5.5"
        base_url = args.base_url or "https://openrouter.ai/api/v1"
        api_key_env = args.api_key_env or "OPENROUTER_API_KEY"
        os.environ.get(api_key_env) or sys.exit(f"{api_key_env} required for --llm-provider openrouter")
        args.use_responses_api = False
    else:
        raise ValueError(f"unknown llm provider {llm_provider!r}")

    llm_settings = {
        "llm_provider": llm_provider,
        "model_name": model_name,
        "base_url": base_url,
        "use_responses_api": args.use_responses_api,
        "reasoning_effort": args.reasoning_effort,
    }

    questions_file = Path(args.questions)
    out_file = Path(args.out) if args.out else OUT_DIR / f"results_v3_{args.tool_set}_{int(time.time())}.json"
    print(f"Output: {out_file}")
    print(f"Questions: {questions_file}")
    print(f"tool_set={args.tool_set}  concurrency={args.concurrency}  per_question_timeout={PER_QUESTION_TIMEOUT_S}s")
    print(f"llm_provider={llm_provider}  model={model_name}  seed={SEED}")

    existing_results = {}
    if out_file.exists():
        try:
            existing = json.load(open(out_file))
            existing_results = existing.get("benchmarking_results", [{}])[0].get("single_question_results", {})
            print(f"  resume: loaded {len(existing_results)} prior results")
        except Exception as e:
            print(f"  resume: failed to load existing ({e})")

    def should_skip(qid):
        prev = existing_results.get(qid)
        if not prev: return False
        if prev.get("error") and args.retry_errored: return False
        return True

    pipeline = make_pipeline(args.tool_set, llm_settings)
    pipeline_name = pipeline.__class__.__name__
    print(f"  pipeline: {pipeline_name} instantiated")

    vectorstore = None
    if args.tool_set == "rag":
        if not ARCHI_DM_URL:
            sys.exit("ARCHI_DM_URL required for --tool-set rag")
        vectorstore = HTTPHybridVectorstore(ARCHI_DM_URL)
        # Sanity: one call
        try:
            hits = vectorstore.hybrid_search("test", k=1)
            print(f"  vectorstore (HTTP hybrid) reachable; first probe returned {len(hits)} hit(s)")
        except Exception as e:
            print(f"WARN: vectorstore probe failed: {type(e).__name__}: {e}", file=sys.stderr)

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
    print(f"  questions: total={len(questions)}  skipped(done)={skipped}  todo={len(todo)}")

    single_q_results = dict(existing_results)
    save_lock = asyncio.Lock()

    async def process_one(i, q, qid):
        qtext = q.get("question", q.get("text", ""))
        print(f"--- [{i+1}/{len(questions)}] START {qid}: {qtext[:80]}", flush=True)
        result = await run_one(qid, qtext, pipeline, vectorstore,
                               pipeline_name=pipeline_name, model_name=model_name)
        tag = "ERR" if result.get("error") else "OK"
        n_hits = len(result.get("sources_metadata") or [])
        print(f"--- [{i+1}/{len(questions)}] DONE  {qid} {tag}: "
              f"time={result['time_elapsed']:.1f}s ans={len(result['answer'])}ch hits={n_hits}", flush=True)
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
    # Force exit — a question's underlying thread (from asyncio.to_thread)
    # may still be blocked on an HTTP call after we timeout-cancelled the
    # future. Non-daemon threads block process exit; os._exit kills the
    # process so the SLURM dependency chain can fire the next job.
    os._exit(0)
