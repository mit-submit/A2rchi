import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error as url_error
from urllib import request as url_request

import openai
import pandas as pd
import yaml
from datasets import Dataset
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from ragas import RunConfig, evaluate
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import (ContextPrecision, ContextRecall, Faithfulness,
                           ResponseRelevancy)

from src.archi.archi import archi
from src.archi.pipelines.agents.agent_spec import AgentSpecError, load_agent_spec
from src.archi.providers import get_model
from src.utils.env import read_secret
from src.utils.logging import get_logger, setup_logging
from src.utils.postgres_service_factory import PostgresServiceFactory

CONFIG_PATH = "/root/archi/config.yaml"
OUTPUT_PATH = "/root/archi/benchmarks"
EXTRA_METADATA_PATH = "/root/archi/git_info.yaml"
OUTPUT_DIR = Path(OUTPUT_PATH)

setup_logging()
logger = get_logger(__name__)
print("[BENCH] service_benchmark.py loaded, logging initialized", flush=True)


@dataclass
class ABResult:
    """Paired A/B comparison result for a single question."""
    question: str
    reference_answer: str
    answer_a: str
    answer_b: str
    time_a: float
    time_b: float
    ragas_a: Dict[str, float] = field(default_factory=dict)
    ragas_b: Dict[str, float] = field(default_factory=dict)
    sources_a: List[Dict[str, Any]] = field(default_factory=list)
    sources_b: List[Dict[str, Any]] = field(default_factory=list)
    messages_a: List[Dict[str, Any]] = field(default_factory=list)
    messages_b: List[Dict[str, Any]] = field(default_factory=list)
    winner_by_metric: Dict[str, str] = field(default_factory=dict)
    llm_judge_a: Dict[str, Any] = field(default_factory=dict)
    llm_judge_b: Dict[str, Any] = field(default_factory=dict)
    llm_judge_pairwise: Dict[str, Any] = field(default_factory=dict)

for _key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HUGGING_FACE_HUB_TOKEN"):
    _val = read_secret(_key)
    if _val:
        os.environ[_key] = _val

factory = PostgresServiceFactory.from_env(password_override=os.environ.get("PG_PASSWORD"))
PostgresServiceFactory.set_instance(factory)


class ResultHandler:
    def __init__(self):
        self.results = []  # store the results for each config
        self.metadata = {}  # store the metadata about the benchmark run
        self.ab_comparison = {}  # single-pair compat (populated only in ab_mode with 2 configs)
        self.ab_comparisons = []  # multi-pair: list of pair comparison dicts

    @staticmethod
    def map_prompts(config: Dict[str, Any]):
        prompts = config.get("services", {}).get("benchmarking", {}).get("prompts")
        if not isinstance(prompts, dict):
            return
        for _, section in prompts.items():
            if not isinstance(section, dict):
                continue
            for prompt_name, file_path in section.items():
                if not file_path:
                    continue
                path = Path(file_path)
                if not path.exists():
                    continue
                with open(path, "r") as f:
                    prompt_str = f.read()
                section[prompt_name] = prompt_str


    @staticmethod
    def handle_results(config_path: Path, results: Dict, total_results: Dict):
        with open(config_path, "r") as f: 
            config = yaml.safe_load(f)

        ResultHandler.map_prompts(config)

        current_results = { 
            "single_question_results": results, 
            "total_results": total_results, 
            "configuration_file": str(config_path),
            "configuration": config, 
        }

        _result_handler.results.append(current_results)

    @staticmethod
    def add_metadata():
        with open(EXTRA_METADATA_PATH, "r") as f: 
            additional_info = yaml.safe_load(f)

        meta_data = {
            "time": str(datetime.now(timezone.utc)),
            "git_info": additional_info, 
        }

        _result_handler.metadata.update(meta_data)


    @staticmethod 
    def dump(benchmark_name: Path):
        filename = f"{benchmark_name}-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        file_path = OUTPUT_DIR / filename
        logger.info(f"Dumping results to {file_path}")
        logger.debug(f"Full results: {_result_handler.results}")
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        output = {
            "benchmarking_results": _result_handler.results,
            "metadata": _result_handler.metadata,
        }
        # Backward compat: single-pair ab_comparison
        if _result_handler.ab_comparison:
            output["ab_comparison"] = _result_handler.ab_comparison
        # Multi-pair ab_comparisons
        if _result_handler.ab_comparisons:
            output["ab_comparisons"] = _result_handler.ab_comparisons
        with open(file_path, "w") as f:
            json.dump(output, f, indent=4)

    @staticmethod
    def pair_ab_results(idx_a: int = 0, idx_b: int = 1) -> List[ABResult]:
        """Pair results from two benchmark configs into ABResult objects.
        
        Args:
            idx_a: Index of first config in results list.
            idx_b: Index of second config in results list.
        """
        if idx_a >= len(_result_handler.results) or idx_b >= len(_result_handler.results):
            raise ValueError(
                f"Result indices ({idx_a}, {idx_b}) out of range for {len(_result_handler.results)} results"
            )

        results_a = _result_handler.results[idx_a]["single_question_results"]
        results_b = _result_handler.results[idx_b]["single_question_results"]

        ragas_metrics = ["answer_relevancy", "faithfulness", "context_precision", "context_recall"]

        paired: List[ABResult] = []
        all_keys = list(results_a.keys()) + [k for k in results_b if k not in results_a]
        for key in all_keys:
            if key not in results_a:
                logger.warning("Question key %s not found in config A results, skipping.", key)
                continue
            if key not in results_b:
                logger.warning("Question key %s not found in config B results, skipping.", key)
                continue
            qa = results_a[key]
            qb = results_b[key]

            ragas_a = {m: qa.get(m, float("nan")) for m in ragas_metrics if m in qa}
            ragas_b = {m: qb.get(m, float("nan")) for m in ragas_metrics if m in qb}

            winner_by_metric: Dict[str, str] = {}
            for m in ragas_a:
                sa, sb = ragas_a.get(m, float("nan")), ragas_b.get(m, float("nan"))
                if math.isnan(sa) or math.isnan(sb):
                    winner_by_metric[m] = "tie"
                elif abs(sa - sb) < 1e-9:
                    winner_by_metric[m] = "tie"
                elif sa > sb:
                    winner_by_metric[m] = "a"
                else:
                    winner_by_metric[m] = "b"

            paired.append(ABResult(
                question=qa["question"],
                reference_answer=qa.get("reference_answer", ""),
                answer_a=qa.get("answer", ""),
                answer_b=qb.get("answer", ""),
                time_a=qa.get("time_elapsed", 0.0),
                time_b=qb.get("time_elapsed", 0.0),
                ragas_a=ragas_a,
                ragas_b=ragas_b,
                sources_a=qa.get("sources_metadata", []),
                sources_b=qb.get("sources_metadata", []),
                messages_a=qa.get("messages", []),
                messages_b=qb.get("messages", []),
                winner_by_metric=winner_by_metric,
                llm_judge_a={k.replace("llm_judge_", ""): v for k, v in qa.items() if k.startswith("llm_judge_")},
                llm_judge_b={k.replace("llm_judge_", ""): v for k, v in qb.items() if k.startswith("llm_judge_")},
            ))

        return paired

    @staticmethod
    def dump_ab_comparison(paired: List[ABResult], idx_a: int = 0, idx_b: int = 1):
        """Build an ab_comparison section from paired results.
        
        When called with default indices (0, 1), also sets ab_comparison
        for backward compatibility.
        """
        config_a = _result_handler.results[idx_a].get("configuration", {})
        config_b = _result_handler.results[idx_b].get("configuration", {})
        bench_a = config_a.get("services", {}).get("benchmarking", {})
        bench_b = config_b.get("services", {}).get("benchmarking", {})

        config_a_meta = {
            "name": bench_a.get("name", f"config_{idx_a}"),
            "agent_class": bench_a.get("agent_class", ""),
            "model": bench_a.get("model", ""),
            "provider": bench_a.get("provider", ""),
            "config_file": _result_handler.results[idx_a].get("configuration_file", ""),
        }
        config_b_meta = {
            "name": bench_b.get("name", f"config_{idx_b}"),
            "agent_class": bench_b.get("agent_class", ""),
            "model": bench_b.get("model", ""),
            "provider": bench_b.get("provider", ""),
            "config_file": _result_handler.results[idx_b].get("configuration_file", ""),
        }

        per_question = [asdict(r) for r in paired]

        # Aggregate wins/losses/ties across all metrics
        wins_a, wins_b, ties = 0, 0, 0
        all_metrics = set()
        for r in paired:
            for m, w in r.winner_by_metric.items():
                all_metrics.add(m)
                if w == "a":
                    wins_a += 1
                elif w == "b":
                    wins_b += 1
                else:
                    ties += 1

        # Mean scores per metric per config
        mean_scores_a: Dict[str, float] = {}
        mean_scores_b: Dict[str, float] = {}
        for m in all_metrics:
            vals_a = [r.ragas_a.get(m) for r in paired if r.ragas_a.get(m) is not None and not math.isnan(r.ragas_a.get(m))]
            vals_b = [r.ragas_b.get(m) for r in paired if r.ragas_b.get(m) is not None and not math.isnan(r.ragas_b.get(m))]
            mean_scores_a[m] = sum(vals_a) / len(vals_a) if vals_a else 0.0
            mean_scores_b[m] = sum(vals_b) / len(vals_b) if vals_b else 0.0

        comparison = {
            "config_a": config_a_meta,
            "config_b": config_b_meta,
            "per_question": per_question,
            "aggregate": {
                "wins_a": wins_a,
                "wins_b": wins_b,
                "ties": ties,
                "mean_scores_a": mean_scores_a,
                "mean_scores_b": mean_scores_b,
            },
        }

        _result_handler.ab_comparisons.append(comparison)

        # Backward compat: also set ab_comparison for first pair
        if idx_a == 0 and idx_b == 1:
            _result_handler.ab_comparison = comparison

    @staticmethod
    def generate_pairwise_combinations(n_configs: int) -> List[Tuple[int, int]]:
        """Generate all pairwise index combinations for N configs."""
        return list(combinations(range(n_configs), 2))


# Module-level singleton instance
_result_handler = ResultHandler()


class Benchmarker: 

    def __init__(self, configs: Path, q_to_a: Dict[str, str]):
        self.queries_to_answers = q_to_a 
        self.required_fields = ['question']
        self.benchmark_name = os.environ['container_name']
        self.all_config_files = self.get_all_configs(configs)
        self.chain = None 
        self.config = None 
        self.current_config = None 

        # Load the first config immediately
        self._load_config_by_path(self.all_config_files[0])
        self.remaining_config_files = self.all_config_files[1:]

        # A/B mode: check if enabled and validate config count
        self.ab_mode = self.benchmarking_configs.get("ab_mode", False)
        if self.ab_mode:
            num_configs = len(self.all_config_files)
            if num_configs < 2:
                raise ValueError(
                    f"A/B mode requires at least 2 benchmark config files, but found {num_configs}."
                )
            max_configs = int(os.environ.get("AB_MAX_CONFIGS", "6"))
            if num_configs > max_configs:
                raise ValueError(
                    f"A/B mode limited to {max_configs} configs ({num_configs} found, "
                    f"which would produce {num_configs * (num_configs - 1) // 2} pairs). "
                    f"Set AB_MAX_CONFIGS env var to override."
                )
            # Validate config names
            self._validate_config_names()
            logger.info("A/B comparison mode enabled with %d configs (%d pairs).",
                        num_configs, num_configs * (num_configs - 1) // 2)

    def _validate_config_names(self):
        """Ensure each config has a unique 'name' field under services.benchmarking."""
        names = []
        for config_path in self.all_config_files:
            with open(config_path, "r") as f:
                cfg = yaml.safe_load(f)
            name = cfg.get("services", {}).get("benchmarking", {}).get("name")
            if not name:
                raise ValueError(
                    f"Config '{config_path}' is missing a 'name' field under "
                    f"services.benchmarking. Each config must have a unique name for A/B mode."
                )
            names.append((name, config_path))
        seen = {}
        for name, path in names:
            if name in seen:
                raise ValueError(
                    f"Duplicate config name '{name}' in '{path}' and '{seen[name]}'. "
                    f"Each config must have a unique name."
                )
            seen[name] = path
    
    def get_all_configs(self, configs_dir):
        all_paths = []
        for root, _, filenames in os.walk(configs_dir):
            for file in sorted(filenames):
                if not file.endswith(('.yaml', '.yml')):
                    continue
                full_path = os.path.join(root, file)
                all_paths.append(full_path)
        result = sorted(all_paths)
        # In multi-config mode, config.yaml is a copy of the first named config
        # (written for config-seed). Skip it to avoid duplicates.
        if len(result) > 1:
            result = [p for p in result if os.path.basename(p) != 'config.yaml']
        return result

    def _load_config_by_path(self, config_path):
        """Load a configuration file and set up the pipeline."""
        self.current_config = config_path
        with open(self.current_config, "r") as f:
            config = yaml.safe_load(f)

        with open(CONFIG_PATH, 'w') as f: 
            yaml.dump(config, stream=f)

        # Re-seed config to postgres so get_full_config() returns updated values.
        # Without this, rerun configs (e.g. switching from CompOps to Copilot)
        # would get stale providers/services from the initial config-seed.
        try:
            from src.cli.tools.config_seed import seed as config_seed
            cs = factory.config_service
            config_seed(config, cs)
            logger.info("Re-seeded config to postgres for %s", config_path)
        except Exception:
            logger.warning("Failed to re-seed config to postgres", exc_info=True)

        self.config = config 
        self.benchmarking_configs = config['services']['benchmarking']
        modes = self.benchmarking_configs.get('modes') or []
        self.required_fields = ['question']
        if 'SOURCES' in modes:
            self.required_fields.append('sources')
        # RAGAS answer_relevancy and faithfulness don't need reference answers

        # for now it only uses one pipeline (the first one) but maybe later we make this work for mulitple
        logger.info(f"loaded new configuration: {self.current_config}")
        benchmark_cfg = config.get("services", {}).get("benchmarking", {}) if isinstance(config, dict) else {}
        pipeline = benchmark_cfg.get("agent_class")
        provider = benchmark_cfg.get("provider")
        model = benchmark_cfg.get("model")
        agent_md_file = benchmark_cfg.get("agent_md_file")
        ollama_url = benchmark_cfg.get("ollama_url")
        missing = [k for k, v in {
            "agent_class": pipeline,
            "provider": provider,
            "model": model,
            "agent_md_file": agent_md_file,
        }.items() if not v]
        if missing:
            raise ValueError(
                f"Missing required benchmarking runtime fields in services.benchmarking: {', '.join(missing)}"
            )
        if str(provider).lower() == "local" and not ollama_url:
            raise ValueError(
                "Missing required benchmarking runtime field in services.benchmarking: ollama_url (required when provider is local)"
            )
        if ollama_url:
            os.environ["OLLAMA_HOST"] = str(ollama_url)

        agent_spec = None
        try:
            agent_spec = load_agent_spec(Path(str(agent_md_file)))
        except AgentSpecError as exc:
            raise ValueError(f"Failed to load benchmark agent spec '{agent_md_file}': {exc}") from exc

        # Store chain creation args for building parallel workers
        self._chain_kwargs = dict(
            pipeline=pipeline,
            agent_spec=agent_spec,
            default_provider=provider,
            default_model=model,
            prompt_overrides={},
        )
        self.chain = archi(
            pipeline,
            agent_spec=agent_spec,
            default_provider=provider,
            default_model=model,
            prompt_overrides={},
        )
        print(f"[BENCH] Chain created: pipeline={pipeline} provider={provider} model={model}", flush=True)

    def _create_chain_pool(self, n_workers: int) -> list:
        """Create a pool of independent chain instances for parallel execution."""
        chains = [self.chain]
        kw = self._chain_kwargs
        for _ in range(n_workers - 1):
            chains.append(archi(
                kw["pipeline"],
                agent_spec=kw["agent_spec"],
                default_provider=kw["default_provider"],
                default_model=kw["default_model"],
                prompt_overrides=kw["prompt_overrides"],
            ))
        logger.info("Created pool of %d chain instances for parallel execution.", n_workers)
        return chains

    def _prefetch_questions_parallel(
        self, n_workers, config_num, total_configs, total_questions, run_start,
    ):
        """Run all questions in parallel using a pool of independent chain instances.

        Returns a dict mapping 1-based question_id to (result, elapsed_seconds).
        """
        chains = self._create_chain_pool(n_workers)
        logger.info("Prefetching %d questions with %d parallel workers...", total_questions, n_workers)

        def _ask(chain, question_id, question_text):
            formatted = [("User", question_text)]
            start = time.perf_counter()
            result, trace_events = self._invoke_with_trace(chain, formatted)
            elapsed = time.perf_counter() - start
            logger.info(
                "[Config %d/%d] Question %d/%d finished (%.2fs)",
                config_num, total_configs, question_id, total_questions, elapsed,
            )
            return question_id, result, elapsed, trace_events

        results = {}
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {}
            for idx, question_item in enumerate(self.queries_to_answers):
                if not isinstance(question_item, dict):
                    continue
                if not all(f in question_item for f in self.required_fields):
                    continue
                qid = idx + 1
                chain = chains[idx % n_workers]
                future = executor.submit(_ask, chain, qid, question_item["question"])
                futures[future] = qid

            for future in as_completed(futures):
                try:
                    qid, result, elapsed, trace_events = future.result()
                    results[qid] = (result, elapsed, trace_events)
                except Exception as exc:
                    qid = futures[future]
                    logger.exception("Question %d failed in parallel execution", qid)
                    # Record a sentinel error result so the question is not silently dropped
                    error_result = {
                        "answer": f"[PARALLEL_ERROR: {exc}]",
                        "messages": [],
                        "source_documents": [],
                        "metadata": {"usage": None, "error": str(exc)},
                    }
                    results[qid] = (error_result, 0.0, [])

        wall_elapsed = time.perf_counter() - run_start
        mins, secs = divmod(int(wall_elapsed), 60)
        logger.info(
            "Parallel prefetch complete: %d/%d questions in %dm%02ds wall time.",
            len(results), total_questions, mins, secs,
        )
        return results

    def _invoke_with_trace(self, chain, history):
        """Invoke a chain, using stream() when available to capture the full event trace.

        Captures every event the pipeline emits:
        - tool_start / tool_output / tool_end  (tool lifecycle + timing)
        - thinking_start / thinking_end         (reasoning chain + duration)
        - text                                  (accumulated answer chunks)
        - error                                 (context overflow, SDK errors)
        - final                                 (terminal output)

        For BaseReActAgent streams, tool data lives in the yielded messages.
        For CopilotAgent streams, tool data lives directly in metadata.
        Both are normalised into the same trace_events schema.

        Returns:
            (result, trace_events)  where result is a PipelineOutput/dict and
            trace_events is a list of structured trace event dicts (empty for
            classic pipelines that don't support streaming).
        """
        if not chain.supports_stream():
            # Classic pipeline (QAPipeline, BareLLMPipeline) — no trace events
            result = chain(history=history)
            return result, []

        # Agent pipeline — stream to capture tool timing, thinking, timestamps
        trace_events = []
        final_result = None
        emitted_tool_starts = set()
        text_chunk_count = 0

        for output in chain.stream(history=history):
            metadata = output.metadata or {}
            event_type = metadata.get("event_type", "")
            timestamp = time.time()

            if event_type == "tool_start":
                # BaseReActAgent: tool calls live inside AIMessage.tool_calls
                for msg in (output.messages or []):
                    for tc in (getattr(msg, "tool_calls", None) or []):
                        tc_id = tc.get("id", "")
                        if tc_id and tc_id not in emitted_tool_starts:
                            emitted_tool_starts.add(tc_id)
                            trace_events.append({
                                "type": "tool_start",
                                "tool_call_id": tc_id,
                                "tool_name": tc.get("name"),
                                "tool_args": tc.get("args", {}),
                                "timestamp": timestamp,
                            })
                # CopilotAgent: tool data lives directly in metadata
                if not output.messages and metadata.get("tool_call_id"):
                    tc_id = metadata["tool_call_id"]
                    if tc_id not in emitted_tool_starts:
                        emitted_tool_starts.add(tc_id)
                        trace_events.append({
                            "type": "tool_start",
                            "tool_call_id": tc_id,
                            "tool_name": metadata.get("tool_name"),
                            "tool_args": metadata.get("tool_args", {}),
                            "timestamp": timestamp,
                        })

            elif event_type == "tool_output":
                # BaseReActAgent: output in ToolMessage
                tool_call_id = metadata.get("tool_call_id", "")
                tool_output_text = metadata.get("output", "")
                if not tool_call_id:
                    for msg in (output.messages or []):
                        if hasattr(msg, "tool_call_id"):
                            tool_call_id = getattr(msg, "tool_call_id", "")
                            tool_output_text = getattr(msg, "content", "")
                            break
                trace_events.append({
                    "type": "tool_output",
                    "tool_call_id": tool_call_id,
                    "output": tool_output_text,
                    "output_length": len(tool_output_text),
                    "timestamp": timestamp,
                })

            elif event_type == "tool_end":
                # CopilotAgent emits tool_end with precomputed duration_ms
                trace_events.append({
                    "type": "tool_end",
                    "tool_call_id": metadata.get("tool_call_id", ""),
                    "status": metadata.get("status", ""),
                    "duration_ms": metadata.get("duration_ms"),
                    "timestamp": timestamp,
                })

            elif event_type == "thinking_start":
                trace_events.append({
                    "type": "thinking_start",
                    "step_id": metadata.get("step_id", ""),
                    "timestamp": timestamp,
                })

            elif event_type == "thinking_end":
                trace_events.append({
                    "type": "thinking_end",
                    "step_id": metadata.get("step_id", ""),
                    "duration_ms": metadata.get("duration_ms"),
                    "thinking_content": metadata.get("thinking_content", ""),
                    "timestamp": timestamp,
                })

            elif event_type == "text":
                # Accumulated answer text — record count + final snapshot
                text_chunk_count += 1

            elif event_type == "error":
                trace_events.append({
                    "type": "error",
                    "error_type": metadata.get("error_type", ""),
                    "error": metadata.get("error", ""),
                    "timestamp": timestamp,
                })
                # Error with final=True acts as the terminal output
                if getattr(output, "final", False):
                    final_result = output

            elif event_type == "final":
                final_result = output

        if final_result is None:
            # Shouldn't happen, but fall back to invoke()
            logger.warning("stream() produced no final event, falling back to invoke()")
            result = chain(history=history)
            return result, trace_events

        # Compute per-tool-call duration from trace timestamps for agents
        # that don't emit explicit tool_end events (BaseReActAgent).
        # CopilotAgent already emits tool_end with duration_ms.
        has_tool_end = any(e["type"] == "tool_end" for e in trace_events)
        if not has_tool_end:
            tool_start_times = {}
            for evt in trace_events:
                if evt["type"] == "tool_start":
                    tool_start_times[evt.get("tool_call_id")] = evt["timestamp"]
                elif evt["type"] == "tool_output":
                    tc_id = evt.get("tool_call_id")
                    if tc_id and tc_id in tool_start_times:
                        evt["duration_ms"] = int((evt["timestamp"] - tool_start_times[tc_id]) * 1000)

        # Record text streaming stats
        if text_chunk_count > 0:
            trace_events.append({
                "type": "text_streaming_summary",
                "chunk_count": text_chunk_count,
            })

        return final_result, trace_events


    def get_ragas_llm_evaluator(self):
        ragas_configs = self.config['services']['benchmarking'].get('mode_settings', {}).get('ragas_settings', {})
        benchmark_cfg = self.config.get("services", {}).get("benchmarking", {})

        # Allow ragas_settings to specify a separate evaluator provider/model,
        # falling back to the benchmark provider/model if not set.
        provider = ragas_configs.get("evaluator_provider") or benchmark_cfg.get("provider")
        model_name = ragas_configs.get("evaluator_model") or benchmark_cfg.get("model")
        ollama_url = ragas_configs.get("evaluator_ollama_url") or benchmark_cfg.get("ollama_url")

        match str(provider).lower():
            case "openai":
                return ChatOpenAI(model=model_name)
            case "ollama":
                from langchain_ollama import ChatOllama
                base_url = ollama_url
                return ChatOllama(model=model_name, base_url=base_url,num_predict=-2,model_kwargs={'format': 'json'})
            case "local":
                from langchain_ollama import ChatOllama
                base_url = ollama_url
                return ChatOllama(model=model_name, base_url=base_url,num_predict=-2,model_kwargs={'format': 'json'})
            case "huggingface":
                base_url = ollama_url or "http://localhost:8000/v1"
                return get_model("local", model_name, base_url=base_url, local_mode="openai_compat")
            case "anthropic":
                from langchain_anthropic import ChatAnthropic
                return ChatAnthropic(model=model_name)
            case _:
                logger.warning("Unknown provider '%s' for RAGAS evaluator, falling back to OpenAI.", provider)
                return ChatOpenAI(model=model_name)


    def get_ragas_embedding_model(self):
        ragas_configs = self.config['services']['benchmarking'].get('mode_settings', {}).get('ragas_settings', {})
        embedding_model = ragas_configs.get('embedding_model', 'OpenAI')

        match embedding_model.lower():
            case "openai":
                return OpenAIEmbeddings()
            case "huggingface":
                return HuggingFaceEmbeddings()
            case _:
                return OpenAIEmbeddings()
            

    def prepare_match_fields(self, question_item):

        # either grab the match field(s) from the question item or use the default
        match_fields = question_item.get('source_match_field')
        if not match_fields:
            match_fields = self.benchmarking_configs.get('mode_settings', {}).get('sources_settings', {}).get('default_match_field', ['file_name'])

        # make it to a list if it's passed as a string
        if isinstance(match_fields, str):
            match_fields = [match_fields] if match_fields else []

        n_sources = len(question_item.get('sources', []))
        if not match_fields:
            # hardcode a default if nothing is provided
            match_fields = ['file_name'] * n_sources
        elif len(match_fields) == 1 and n_sources > 1:
            # expand single field to all sources
            match_fields = match_fields * n_sources
        elif len(match_fields) != n_sources:
            logger.error(
                "Number of match fields (%s) does not align with number of reference sources (%s); reusing the last field for the remaining references.",
                len(match_fields),
                n_sources,
            )
            raise ValueError("Mismatch between number of match fields and reference sources.")
        
        return match_fields


    def prepare_reference_sources(self, reference_sources, match_fields):

        # Clean and prepare reference sources
        raw_references: List[str] = []
        if isinstance(reference_sources, str):
            cleaned = reference_sources.strip()
            if cleaned and cleaned != 'N/A':
                raw_references = [reference_sources]
        elif isinstance(reference_sources, list):
            raw_references = [ref for ref in reference_sources if ref not in (None, '')]
        elif reference_sources is None:
            raw_references = []
        else:
            raw_references = [reference_sources]
        reference_sources_list: List[str] = []
        for ref in raw_references:
            ref_str = str(ref).strip()
            if ref_str and ref_str != 'N/A':
                reference_sources_list.append(ref_str)

        formatted_reference_sources = []
        for field, reference in zip(match_fields, reference_sources_list):
            formatted_reference_sources.append({field: reference})

        return formatted_reference_sources


    def prepare_messages(self, raw_messages):
        """Format the langchain Messages into something we can store and view later.
        
        Extracts per-step token usage, model info, and timing from response_metadata.
        """
        import re

        # First pass: index ToolMessage results by tool_call_id
        tool_results = {}
        for msg in raw_messages:
            if type(msg) is ToolMessage:
                tcid = getattr(msg, 'tool_call_id', None)
                if tcid:
                    tool_results[tcid] = getattr(msg, 'content', '')

        formatted_messages = []
        for msg in raw_messages:
            resp_meta = getattr(msg, 'response_metadata', {}) or {}

            # Extract per-step usage from response_metadata
            step_usage = None
            for usage_key in ("usage_metadata", "usage", "token_usage"):
                u = resp_meta.get(usage_key)
                if u and isinstance(u, dict):
                    step_usage = {
                        "prompt_tokens": u.get("prompt_tokens") or u.get("input_tokens", 0),
                        "completion_tokens": u.get("completion_tokens") or u.get("output_tokens", 0),
                        "total_tokens": u.get("total_tokens", 0),
                    }
                    break
            # Ollama format
            if step_usage is None and ("prompt_eval_count" in resp_meta or "eval_count" in resp_meta):
                step_usage = {
                    "prompt_tokens": resp_meta.get("prompt_eval_count", 0),
                    "completion_tokens": resp_meta.get("eval_count", 0),
                    "total_tokens": resp_meta.get("prompt_eval_count", 0) + resp_meta.get("eval_count", 0),
                }
            # Also check usage_metadata directly on the message object (LangChain >=0.2)
            if step_usage is None:
                um = getattr(msg, 'usage_metadata', None)
                if um and isinstance(um, dict):
                    step_usage = {
                        "prompt_tokens": um.get("input_tokens", 0),
                        "completion_tokens": um.get("output_tokens", 0),
                        "total_tokens": um.get("total_tokens", 0),
                    }

            step_model = resp_meta.get("model") or resp_meta.get("model_name")

            if type(msg) is AIMessage:
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    for tool_call in msg.tool_calls:
                        entry = {
                            'type': 'tool_call',
                            'tool_name': tool_call.get('name'),
                            'tool_args': tool_call.get('args', {}),
                            'total_duration': resp_meta.get('total_duration', None),
                        }
                        if step_usage:
                            entry['step_usage'] = step_usage
                        if step_model:
                            entry['step_model'] = step_model
                        tcid = tool_call.get('id')
                        if tcid:
                            entry['tool_call_id'] = tcid
                        if tcid and tcid in tool_results:
                            entry['tool_output'] = tool_results[tcid]
                        formatted_messages.append(entry)
                elif hasattr(msg, 'content'):
                    raw_content = msg.content or ''
                    thinking = ''
                    # OpenAI Responses API returns content as a list of typed blocks
                    # (e.g. [{"type": "text", "text": "..."}, {"type": "reasoning", "text": "..."}]).
                    # Flatten reasoning blocks into `thinking` and concatenate text blocks into `content`.
                    if isinstance(raw_content, list):
                        text_parts = []
                        reasoning_parts = []
                        for block in raw_content:
                            if isinstance(block, dict):
                                btype = block.get('type', '')
                                btext = block.get('text', '') or block.get('content', '')
                                if 'reasoning' in btype.lower() or btype == 'thinking':
                                    if btext:
                                        reasoning_parts.append(str(btext))
                                elif btype in ('text', 'output_text', 'content', ''):
                                    if btext:
                                        text_parts.append(str(btext))
                            elif isinstance(block, str):
                                text_parts.append(block)
                        content = "\n".join(text_parts)
                        if reasoning_parts:
                            thinking = "\n".join(reasoning_parts)
                    else:
                        content = raw_content
                    # Extract <think>...</think> blocks (Qwen3-style)
                    think_pattern = re.compile(r'<think>(.*?)</think>', re.DOTALL)
                    if isinstance(content, str):
                        think_matches = think_pattern.findall(content)
                        if think_matches:
                            thinking = "\n".join(think_matches)
                            content = think_pattern.sub('', content).strip()
                    # Extract reasoning_content (OpenAI o1/o3 style)
                    additional_kwargs = getattr(msg, 'additional_kwargs', None) or {}
                    reasoning = additional_kwargs.get('reasoning_content', '')
                    if reasoning and not thinking:
                        thinking = str(reasoning)
                    entry = {
                        'type': 'ai_message',
                        'content': content,
                        'total_duration': resp_meta.get('total_duration', None),
                    }
                    if thinking:
                        entry['thinking'] = thinking
                    if step_usage:
                        entry['step_usage'] = step_usage
                    if step_model:
                        entry['step_model'] = step_model
                    formatted_messages.append(entry)
            elif type(msg) is HumanMessage:
                pass
            elif type(msg) is ToolMessage:
                # Handled via tool_results lookup above
                pass
            else:
                logger.warning(f"Unexpected message type: {type(msg)}")
        return formatted_messages


    def get_source_results(
            self,
            result: Dict,
            formatted_reference_sources: List[Dict[str, str]],
        ) -> List[bool]:
        """
        For each reference source, check the specified metadata field in the retrieved documents.
        The reference sources and match fields are paired one-to-one; a single string field is
        expanded to cover all provided sources. Returns summary information and whether all
        reference sources were found.
        """
        sources = result.get('source_documents', [])
        logger.info("Agent found %s sources.", len(sources))
        
        matches: List[bool] = []
        for source in formatted_reference_sources:
            field, reference = list(source.items())[0]
            logger.debug("Checking for reference source '%s' in field '%s'", reference, field)
            for document in sources:
                metadata = getattr(document, 'metadata', {}) or {}
                value = metadata.get(field)
                if value is None:
                    continue
                if isinstance(value, list):
                    values = [str(v).strip() for v in value if v is not None]
                else:
                    values = [str(value).strip()]
                logger.info("Returned source '%s': %s", field, values)
                logger.debug("Checking reference '%s' against document metadata field '%s': %s", reference, field, values)
                if reference in values:
                    logger.debug("Matched reference source '%s' in document metadata.", reference)
                    matches.append(True)
                    break
            else:
                matches.append(False)

        # match is determined if at least once source is found
        logger.info("Source matching result: %s", matches)
        return matches


    def get_ragas_results(self, data, to_add):
        """WARNING: this method modifies the to_add dictionary to add the relevant scores to the relevant questions"""
        
        all_metrics_dict = {
                'answer_relevancy': ResponseRelevancy(),
                'faithfulness': Faithfulness(),
                'context_precision': ContextPrecision(),
                'context_recall': ContextRecall(),
                }

        enabled_metrics = self.benchmarking_configs.get('mode_settings', {}).get('ragas_settings', {}).get(
            'enabled_metrics', list(all_metrics_dict.keys())
        )

        metrics_dict = {k: v for k, v in all_metrics_dict.items() if k in enabled_metrics}
                       
        res = pd.DataFrame()

        ragas_settings = self.config['services']['benchmarking'].get('mode_settings', {}).get('ragas_settings', {})
        log_tenacity = self.config.get('global', {}).get('verbosity', 0) >= 4
        timeout = ragas_settings.get('timeout', 180)
        batch_settings = ragas_settings.get('batch_size', None)
        if not batch_settings: 
            batch_settings = None
        
        runconfig = RunConfig(timeout=timeout, log_tenacity=log_tenacity)
        # going one metric at a time prevents errors 
        for metric_name, metric in metrics_dict.items():
            evaluation_results = evaluate(data, 
                                          metrics=[metric],
                                          llm=LangchainLLMWrapper(self.get_ragas_llm_evaluator()),
                                          embeddings=LangchainEmbeddingsWrapper(self.get_ragas_embedding_model()),
                                          run_config=runconfig,
                                          batch_size=batch_settings
                                          )

            metric_results = evaluation_results.to_pandas()
            # Use the metric's internal name for DataFrame column lookup
            col_name = metric.name if hasattr(metric, 'name') else metric_name
            res[metric_name] = metric_results[col_name]

        for question_idx, question in enumerate(to_add.values()):
            for metric in metrics_dict.keys():
                question[metric] = res.at[question_idx, metric]

        return res

    # ── LLM-as-Judge (v4 rubric — reference-free, anti-length-bias) ──

    _LLM_JUDGE_RUBRICS = {
        "relevance": (
            "**Relevance** — Does the answer address the specific question that was asked?\n"
            "- 5: Directly and precisely addresses the question — every part of the response is on-topic\n"
            "- 4: Addresses the question with minor tangential content\n"
            "- 3: Partially addresses the question but includes significant off-topic material, "
            "or only addresses part of a multi-part question\n"
            "- 2: Mostly off-topic — touches on the general subject area but does not answer what was asked\n"
            "- 1: Completely irrelevant, or a non-response (\"I'm ready to help!\", empty, greeting-only)"
        ),
        "completeness": (
            "**Completeness** — How many aspects of the question does the answer address?\n"
            "Assess scope by inferring what a full answer would need to cover from the question itself. "
            "For multi-part questions, a complete answer addresses all parts.\n"
            "- 5: Addresses all aspects of the question — no significant gaps\n"
            "- 4: Addresses most aspects, one minor gap\n"
            "- 3: Addresses the core question but misses important context or sub-questions\n"
            "- 2: Only partially addresses the question — significant gaps\n"
            "- 1: Does not meaningfully address the question, or is a non-response"
        ),
        "specificity": (
            "**Specificity** — Does the answer provide concrete, actionable details — or only vague generalities?\n"
            "Concrete details include: specific commands, configuration values, ticket numbers, data values, "
            "step-by-step procedures, tool names with usage instructions, dates, error codes with explanations.\n\n"
            "CRITICAL GUARDRAIL — unsupported specifics vs. honest vagueness:\n"
            "An answer that provides specific details *grounded in cited sources or tool output* should score high. "
            "An answer that provides specific details *without any supporting evidence* (no citations, no tool output, "
            "no documentation references) should score LOWER than an answer that is honestly vague — because "
            "unsupported specifics may be fabricated and would mislead an operator.\n\n"
            "- 5: Rich in concrete, well-supported details — commands, data, ticket references, "
            "step-by-step procedures grounded in sources or tool output\n"
            "- 4: Provides useful specific details, mostly supported; minor unsupported claims\n"
            "- 3: Mix of specific and vague — some actionable content but also generic advice "
            "(\"check the logs\", \"contact the team\")\n"
            "- 2: Mostly vague or generic advice with little actionable content, OR provides unsupported "
            "specifics without any citations/evidence\n"
            "- 1: Entirely vague (\"look into it\"), a refusal with no guidance, or a non-response"
        ),
        "helpfulness": (
            "**Helpfulness** — Would a CMS computing operator be able to make progress on their task using this answer?\n"
            "This is the bottom-line pragmatic dimension. An answer can be relevant, complete, and specific "
            "but still unhelpful if it points in the wrong direction.\n"
            "- 5: An operator could act on this answer immediately — clear, correct next steps with enough detail to execute\n"
            "- 4: Useful — provides a path forward, may require minor follow-up to fully act on\n"
            "- 3: Somewhat useful — gives the operator a starting point but requires significant additional investigation\n"
            "- 2: Minimally useful — vague pointers or a refusal with no alternative guidance\n"
            "- 1: Not useful or actively harmful — would send the operator in the wrong direction, or is a non-response"
        ),
        "source_faithfulness": (
            "**Source Faithfulness** — Does the answer accurately reflect what its own retrieved sources "
            "and tool output say?\n"
            "This evaluates internal consistency between the answer and the sources it was given — "
            "NOT whether the sources themselves are correct.\n"
            "- 5: All key claims in the answer are directly supported by the provided sources; no misrepresentation\n"
            "- 4: Most claims are supported by sources; minor extrapolations that are reasonable\n"
            "- 3: Mix of supported and unsupported claims — some content goes beyond what sources say\n"
            "- 2: Significant misrepresentation of sources, or answer largely ignores source content\n"
            "- 1: Answer contradicts its own sources, or makes extensive claims with no source support "
            "despite sources being available"
        ),
    }

    _BASE_DIMENSIONS = ["relevance", "completeness", "specificity", "helpfulness"]

    def _get_llm_judge_settings(self) -> Dict[str, Any]:
        return self.benchmarking_configs.get("mode_settings", {}).get("llm_judge_settings", {})

    def _get_llm_judge_client(self) -> openai.OpenAI:
        settings = self._get_llm_judge_settings()
        api_key = settings.get("api_key") or os.environ.get("OPENAI_API_KEY")
        base_url = settings.get("base_url")
        kwargs: Dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        return openai.OpenAI(**kwargs)

    def _get_dimensions(self, has_sources: bool = False) -> List[str]:
        """Return judge dimensions; adds source_faithfulness when sources are present."""
        dims = list(self._BASE_DIMENSIONS)
        if has_sources:
            dims.append("source_faithfulness")
        return dims

    def _build_absolute_prompt(self, dimensions: List[str], question: str,
                               generated_answer: str, **_kwargs) -> str:
        rubric_parts = [self._LLM_JUDGE_RUBRICS[d] for d in dimensions if d in self._LLM_JUDGE_RUBRICS]
        rubric_text = "\n\n".join(rubric_parts)
        dim_keys = ", ".join(f'\"{d}\"' for d in dimensions)
        return (
            "You are an expert evaluator for a CMS Computing Operations AI assistant. "
            "Evaluate the generated answer on the following dimensions using a 1\u20135 scale.\n\n"
            "IMPORTANT PRINCIPLES:\n"
            "- This is a REFERENCE-FREE evaluation. Score based on the answer's own quality alone.\n"
            "- Unsupported specific claims (invented ticket numbers, dates, data values with no "
            "cited source) are WORSE than honest vagueness.\n"
            "- Non-responses (\"I'm ready to help!\", empty answers, greetings) score 1 on all dimensions.\n"
            "- ANTI-LENGTH BIAS: Do NOT reward longer answers for being longer. A concise, accurate "
            "answer should score as high or higher than a verbose answer that pads with generic advice. "
            "Score based on information quality, not quantity.\n\n"
            f"{rubric_text}\n\n"
            f"Question:\n{question}\n\n"
            f"Generated Answer:\n{generated_answer}\n\n"
            "Evaluate each dimension individually BEFORE assigning any scores. "
            "Think step-by-step about what the question asks, what the answer provides, "
            "and how well the answer serves an operator.\n\n"
            f'Return a JSON object with:\n'
            f'  - "reasoning": your step-by-step analysis (2-4 sentences)\n'
            f'  - integer scores (1-5) for each of: {dim_keys}'
        )

    def _build_pairwise_prompt(self, question: str,
                               response_first: str, response_second: str) -> str:
        return (
            "You are an expert evaluator for a CMS computing operations Q&A system.\n\n"
            "Given a question and two candidate responses, determine which response "
            "is better overall for a CMS operator.\n\n"
            f"Question:\n{question}\n\n"
            f"Response A:\n{response_first}\n\n"
            f"Response B:\n{response_second}\n\n"
            'Return ONLY a JSON object: {"winner": "A" or "B" or "tie", "reasoning": "<brief explanation>"}'
        )

    def _call_llm_judge(self, client: openai.OpenAI, model: str,
                        prompt: str, max_tokens: int = 1024,
                        max_retries: int = 3) -> Dict[str, Any]:
        last_err = None
        for attempt in range(max_retries):
            try:
                response = client.chat.completions.create(
                    model=model,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": "You are an expert evaluator. Always respond with valid JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    max_completion_tokens=max_tokens,
                )
                content = response.choices[0].message.content
                if not content:
                    raise ValueError(f"Empty response (finish_reason={response.choices[0].finish_reason})")
                result = json.loads(content)
                usage = getattr(response, 'usage', None)
                if usage:
                    result["_usage"] = {
                        "prompt_tokens": usage.prompt_tokens,
                        "completion_tokens": usage.completion_tokens,
                        "total_tokens": usage.total_tokens,
                    }
                return result
            except (json.JSONDecodeError, openai.APIStatusError) as e:
                last_err = e
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    if isinstance(e, openai.APIStatusError) and e.status_code == 429:
                        wait = min(2 ** (attempt + 2), 30)
                    logger.warning("LLM judge retry %d/%d (wait %ds): %s", attempt + 1, max_retries, wait, e)
                    time.sleep(wait)
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1:
                    logger.warning("LLM judge retry %d/%d: %s", attempt + 1, max_retries, e)
                    time.sleep(2 ** attempt)
        raise last_err

    def get_llm_judge_results(self, question_wise_results: Dict[str, Dict[str, Any]]) -> Tuple[pd.DataFrame, Dict[str, int]]:
        """Run absolute LLM-as-judge scoring on every question for the current config.

        Modifies *question_wise_results* in-place (adds ``llm_judge_<dim>`` and
        ``llm_judge_reasoning`` keys per question) and returns a tuple of
        (DataFrame of scores, token usage dict).
        """
        settings = self._get_llm_judge_settings()
        model = settings.get("evaluator_model", "gpt-5-2025-08-07")
        parallel_workers = int(settings.get("parallel_workers", 8))

        # Detect if this config has retrieval/tools (for source_faithfulness)
        config_has_sources = any(v.get("sources_trunc_content") for v in question_wise_results.values())

        # Use v4 dimensions (reference-free)
        dimensions = self._get_dimensions(config_has_sources)

        client = self._get_llm_judge_client()

        # Filter to questions that need judging
        to_judge = {k: v for k, v in question_wise_results.items()
                    if f"llm_judge_{dimensions[0]}" not in v and v.get("answer")}

        if not to_judge:
            logger.info("All questions already have judge scores, skipping.")
            return pd.DataFrame(), {"prompt_tokens": 0, "completion_tokens": 0}

        total = len(to_judge)
        logger.info("LLM-as-Judge: scoring %d questions with %d workers, model=%s, dims=%s",
                     total, parallel_workers, model, dimensions)

        def _judge_one(q_key: str, q_data: Dict) -> Tuple[str, Optional[Dict], Optional[str], List[str]]:
            has_sources = bool(q_data.get("sources_trunc_content")) if config_has_sources else False
            dims = self._get_dimensions(has_sources)
            prompt = self._build_absolute_prompt(dims, q_data["question"], q_data["answer"])
            try:
                scores = self._call_llm_judge(client, model, prompt)
                return q_key, scores, None, dims
            except Exception as e:
                return q_key, None, f"{type(e).__name__}: {str(e)[:200]}", dims

        completed = 0
        failed = 0
        all_usages = []

        with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
            futures = {executor.submit(_judge_one, k, v): k for k, v in to_judge.items()}

            for future in as_completed(futures):
                q_key, scores, error, dims = future.result()
                if error:
                    failed += 1
                    for dim in dims:
                        question_wise_results[q_key][f"llm_judge_{dim}"] = float("nan")
                    question_wise_results[q_key]["llm_judge_reasoning"] = f"ERROR: {error}"
                    if failed <= 3:
                        logger.warning("LLM-as-Judge ERROR on %s: %s", q_key, error)
                else:
                    usage = scores.pop("_usage", None)
                    if usage:
                        all_usages.append(usage)
                    for dim in dims:
                        raw = scores.get(dim)
                        question_wise_results[q_key][f"llm_judge_{dim}"] = int(raw) if raw is not None else float("nan")
                    question_wise_results[q_key]["llm_judge_reasoning"] = scores.get("reasoning", "")

                completed += 1
                if completed % 10 == 0 or completed == total:
                    logger.info("LLM-as-Judge: %d/%d done (%d failed)", completed, total, failed)

        # Accumulate tokens from futures (thread-safe — post-completion)
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        for u in all_usages:
            total_usage["prompt_tokens"] += u.get("prompt_tokens", 0)
            total_usage["completion_tokens"] += u.get("completion_tokens", 0)

        total_tokens = total_usage["prompt_tokens"] + total_usage["completion_tokens"]
        logger.info("LLM-as-Judge token usage: %d prompt + %d completion = %d total",
                     total_usage["prompt_tokens"], total_usage["completion_tokens"], total_tokens)

        # Build a DataFrame for aggregate computation
        data: Dict[str, List[float]] = {dim: [] for dim in dimensions}
        for q_data in question_wise_results.values():
            for dim in dimensions:
                data[dim].append(q_data.get(f"llm_judge_{dim}", float("nan")))
        return pd.DataFrame(data), total_usage

    def get_llm_judge_pairwise(self, paired: List[ABResult]) -> List[ABResult]:
        """Run pairwise LLM-as-judge comparison with position swap.

        For each paired question the judge is called twice (A-first then B-first)
        to control for position bias.  The ``llm_judge_pairwise`` field and
        ``winner_by_metric["llm_judge"]`` are set on each *ABResult* in-place.
        """
        settings = self._get_llm_judge_settings()
        model = settings.get("evaluator_model", "gpt-5-2025-08-07")
        client = self._get_llm_judge_client()

        for idx, ab in enumerate(paired):
            logger.info("LLM-as-Judge pairwise %d/%d: %s", idx + 1, len(paired), ab.question[:80])
            try:
                # Pass 1: A-first
                verdict_ab = self._call_llm_judge(
                    client, model, self._build_pairwise_prompt(ab.question, ab.answer_a, ab.answer_b), 512
                )
                # Pass 2: B-first (position swap)
                verdict_ba = self._call_llm_judge(
                    client, model, self._build_pairwise_prompt(ab.question, ab.answer_b, ab.answer_a), 512
                )
            except Exception:
                logger.exception("LLM-as-Judge pairwise failed for question %d", idx)
                ab.llm_judge_pairwise = {"final_winner": "tie", "error": True}
                ab.winner_by_metric["llm_judge"] = "tie"
                continue

            # Map the swapped verdict back to original positions
            swap_map = {"A": "B", "B": "A", "tie": "tie"}
            winner_ab = str(verdict_ab.get("winner", "tie")).upper()
            winner_ba_mapped = swap_map.get(str(verdict_ba.get("winner", "tie")).upper(), "tie")

            # Agree -> use that verdict; disagree -> tie
            if winner_ab == winner_ba_mapped:
                final = winner_ab.lower()  # "a", "b", or "tie"
            else:
                final = "tie"

            ab.llm_judge_pairwise = {
                "verdict_ab": winner_ab.lower(),
                "verdict_ba": winner_ba_mapped.lower(),
                "final_winner": final,
                "reasoning_ab": verdict_ab.get("reasoning", ""),
                "reasoning_ba": verdict_ba.get("reasoning", ""),
            }
            ab.winner_by_metric["llm_judge"] = final

        return paired


    def run(self):
        self.wait_for_ingestion_completion()
        print("[BENCH] Starting benchmark run", flush=True)

        modes_being_run = set(self.benchmarking_configs.get('modes') or [])

        logger.info("")
        logger.info("====== Starting benchmark: %s ======", self.benchmark_name)
        logger.info("Modes being run: %s", modes_being_run)
        total_questions = len(self.queries_to_answers)
        total_configs = len(self.all_config_files)
        logger.info(f"Processing {total_questions} questions and {total_configs} configuration(s).")
        logger.info("")

        run_start = time.perf_counter()
        config_num = 0
        configs_to_run = [self.current_config] + self.remaining_config_files

        # Checkpoint / resume support
        checkpoint_path = OUTPUT_DIR / f"{self.benchmark_name}.checkpoint.json"
        completed_configs = set()
        in_progress = None
        if checkpoint_path.exists():
            checkpoint = self._load_checkpoint(checkpoint_path)
            if checkpoint.get("complete"):
                logger.info("Benchmark already complete (checkpoint found). Skipping.")
                ResultHandler.dump(self.benchmark_name)
                return
            completed_configs = set(checkpoint.get("completed_configs", []))
            # Restore prior results
            for prior_result in checkpoint.get("results", []):
                _result_handler.results.append(prior_result)
            # Restore in-progress partial results for question-level resume
            in_progress = checkpoint.get("in_progress")
            logger.info("Resuming from checkpoint: %d/%d configs already complete.",
                        len(completed_configs), total_configs)
            if in_progress:
                logger.info("In-progress config '%s' has %d/%d questions done — will resume.",
                            in_progress["config_path"],
                            in_progress["question_id"],
                            total_questions)

        # Argilla export setup
        argilla_enabled = os.environ.get("ARGILLA_EXPORT", "").lower() in ("1", "true", "yes")
        argilla_dataset_name = None
        any_llm_judge = False   # track if any config uses LLM_JUDGE mode

        for config_path in configs_to_run:
            config_num += 1

            # Skip completed configs (resume support)
            if config_path in completed_configs:
                logger.info("[Config %d/%d] %s — already complete, skipping.", config_num, total_configs, config_path)
                continue

            if config_path != self.current_config:
                self._load_config_by_path(config_path)
                modes_being_run = set(self.benchmarking_configs.get('modes') or [])

            question_id = 0

            # results for each question
            question_wise_results = {}

            # results for all of the questions in this config
            total_results = {}

            # RAGAS mode: ragas inputs
            ragas_input = []

            # SOURCES mode: sources accuracy
            relative_source_accuracy = 0.0 
            source_accuracy = 0.0

            # Restore partial progress if this config was in-progress
            if in_progress and in_progress.get("config_path") == config_path:
                question_wise_results = in_progress.get("question_wise_results", {})
                question_id = in_progress.get("question_id", 0)
                ragas_input = in_progress.get("ragas_input", [])
                source_accuracy = in_progress.get("source_accuracy", 0.0)
                relative_source_accuracy = in_progress.get("relative_source_accuracy", 0.0)
                logger.info("Restored %d questions from in-progress checkpoint for %s",
                            question_id, config_path)

            parallel_workers = self.benchmarking_configs.get("parallel_workers", 1)
            prefetched_results = {}

            if parallel_workers > 1:
                prefetched_results = self._prefetch_questions_parallel(
                    parallel_workers, config_num, total_configs, total_questions, run_start,
                )

            for q_index, question_item in enumerate(self.queries_to_answers):

                # Skip questions already completed (question-level resume)
                if q_index < question_id:
                    continue

                logger.info("")
                logger.info("====================================")
                logger.info(f"[Config {config_num}/{total_configs}] Question {question_id + 1}/{total_questions}")

                if not isinstance(question_item, dict):
                    logger.error(f"Each item in the question to answer list must be a dictionary, but got {type(question_item)}")
                    continue
                if not all(field in question_item for field in self.required_fields):
                    logger.error(f"Each item in the question to answer list must contain the following fields: {self.required_fields}, but got {question_item.keys()}")
                    continue

                question = question_item['question']
                reference_answer = question_item.get('answer', 'N/A')
                reference_sources = question_item.get('sources', 'N/A')

                logger.info(f"Question: {question}")
                logger.info(f"Reference Answer: {reference_answer}")
                logger.info(f"Reference Sources: {reference_sources}")

                question_id +=1
                formatted_question = [("User", question)]

                if question_id in prefetched_results:
                    result, elapsed, trace_events = prefetched_results[question_id]
                else:
                    question_timeout = int(os.environ.get("BENCH_QUESTION_TIMEOUT", "600"))
                    start = time.perf_counter()
                    print(f"[BENCH] Invoking chain for question {question_id}...", flush=True)
                    future = ThreadPoolExecutor(max_workers=1).submit(
                        self._invoke_with_trace, self.chain, formatted_question,
                    )
                    try:
                        result, trace_events = future.result(timeout=question_timeout)
                    except Exception as timeout_exc:
                        elapsed = time.perf_counter() - start
                        logger.error(
                            "Question %d timed out or failed after %.1fs: %s",
                            question_id, elapsed, timeout_exc,
                        )
                        print(f"[BENCH] Question {question_id} TIMEOUT/FAIL after {elapsed:.1f}s: {timeout_exc}", flush=True)
                        q_results = {
                            "time_elapsed": elapsed,
                            "question": question,
                            "reference_answer": reference_answer,
                            "answer": f"[TIMEOUT after {elapsed:.0f}s: {timeout_exc}]",
                            "messages": [],
                            "token_usage": None,
                            "trace_events": [],
                            "model_used": None,
                            "pipeline_used": None,
                            "thinking_content": "",
                            "reference_sources_match_fields": [],
                            "reference_sources_metadata": [],
                            "sources_metadata": [],
                            "sources_content": [],
                        }
                        question_wise_results[f"question_{question_id}"] = q_results
                        self._save_in_progress_checkpoint(
                            checkpoint_path, config_path, question_wise_results,
                            question_id, ragas_input, source_accuracy, relative_source_accuracy,
                        )
                        continue
                    elapsed = time.perf_counter() - start
                    print(f"[BENCH] Question {question_id} completed in {elapsed:.1f}s", flush=True)

                total_elapsed = time.perf_counter() - run_start
                mins, secs = divmod(int(total_elapsed), 60)
                logger.info(f"Finished question {question_id}/{total_questions} ({elapsed:.2f}s) — total elapsed {mins}m{secs:02d}s")
                q_results = {}

                # prepare info to store for this question
                q_results["time_elapsed"] = elapsed
                q_results["question"] = question
                q_results["reference_answer"] = reference_answer
                q_results["answer"] = result['answer']

                # capture token usage — always present for schema consistency
                result_meta = result.get("metadata", {}) or {}
                q_results["token_usage"] = result_meta.get("usage") or None

                # model and pipeline identification
                q_results["model_used"] = (
                    result_meta.get("model_used")
                    or result_meta.get("model")
                    or None
                )
                q_results["pipeline_used"] = result_meta.get("pipeline_used") or None

                # RAG-specific: retriever scores and condensed question
                q_results["retriever_scores"] = result_meta.get("retriever_scores") or []
                q_results["condensed_question"] = result_meta.get("condensed_output") or None

                # Agent-specific: structured tool inputs map
                q_results["tool_inputs_by_id"] = result_meta.get("tool_inputs_by_id") or {}

                # Full event trace (tool timing, thinking, timestamps) from stream()
                q_results["trace_events"] = trace_events

                # Trace summary stats
                if trace_events:
                    tool_count = sum(1 for e in trace_events if e.get("type") == "tool_start")
                    # Collect tool durations from tool_output (BaseReActAgent) or tool_end (CopilotAgent)
                    tool_durations = [e.get("duration_ms") for e in trace_events
                                      if e.get("type") in ("tool_output", "tool_end")
                                      and e.get("duration_ms") is not None]
                    thinking_durations = [e.get("duration_ms") for e in trace_events
                                          if e.get("type") == "thinking_end" and e.get("duration_ms") is not None]
                    thinking_contents = [e.get("thinking_content", "") for e in trace_events
                                         if e.get("type") == "thinking_end" and e.get("thinking_content")]
                    errors = [e for e in trace_events if e.get("type") == "error"]
                    q_results["trace_summary"] = {
                        "total_tool_calls": tool_count,
                        "tool_durations_ms": tool_durations,
                        "total_tool_time_ms": sum(tool_durations) if tool_durations else 0,
                        "thinking_durations_ms": thinking_durations,
                        "total_thinking_time_ms": sum(thinking_durations) if thinking_durations else 0,
                        "thinking_content": thinking_contents,
                        "errors": errors,
                    }
                else:
                    q_results["trace_summary"] = None

                # Unified thinking_content field — aggregates from agent stream events and
                # classic pipeline metadata so all pipelines surface it the same way.
                pipeline_thinking = result_meta.get("thinking_content") or ""
                trace_thinking_chunks = [
                    e.get("thinking_content", "") for e in (trace_events or [])
                    if e.get("type") == "thinking_end" and e.get("thinking_content")
                ]
                trace_thinking = "\n\n".join(trace_thinking_chunks)
                q_results["thinking_content"] = pipeline_thinking or trace_thinking or ""

                # format the messages (with per-step usage + model)
                q_results['messages'] = self.prepare_messages(result.get("messages", []))

                # Copilot pipelines store tool calls in metadata["tool_calls"]
                # instead of LangChain messages — fall back to those.
                if not q_results['messages']:
                    meta = result.get("metadata", {}) or {}
                    meta_tool_calls = meta.get("tool_calls", [])
                    for tc in meta_tool_calls:
                        q_results['messages'].append({
                            'type': 'tool_call',
                            'tool_name': tc.get('name'),
                            'tool_args': tc.get('args', {}),
                            'tool_output': tc.get('result', ''),
                            'total_duration': None,
                        })
                    if q_results['messages']:
                        q_results['messages'].append({
                            'type': 'ai_message',
                            'content': result.get('answer', ''),
                            'total_duration': None,
                        })

                # format the reference sources (only when SOURCES mode is active)
                if "SOURCES" in modes_being_run:
                    match_fields_list = self.prepare_match_fields(question_item)
                    formatted_reference_sources = self.prepare_reference_sources(reference_sources, match_fields_list)
                    q_results["reference_sources_match_fields"] = match_fields_list
                    q_results["reference_sources_metadata"] = formatted_reference_sources
                else:
                    q_results["reference_sources_match_fields"] = []
                    q_results["reference_sources_metadata"] = []

                if "RAGAS" in modes_being_run:
                    # collect necessary info for RAGAS evaluation
                    source_docs = result.get('source_documents', [])
                    contexts = [s.page_content for s in source_docs] if source_docs else [""]
                    dataset_result = {
                            "question": question,
                            "contexts": contexts,
                            "answer": result['answer'],
                            "ground_truth": reference_answer,
                            }
                    ragas_input.append(dataset_result)

                if "SOURCES" in modes_being_run: 
                    # sources evaluation is done on the fly -- check if each of the given sources was found                  
                    matches = self.get_source_results(
                        result,
                        formatted_reference_sources,
                    )
                    # we count accuracy via any of the sources matching
                    if any(matches): 
                        relative_source_accuracy += 1.0
                    if len(matches) == len(formatted_reference_sources) and all(matches):
                        source_accuracy += 1.0
                    # but we still store the match of each reference source in its metadata
                    for idx, source in enumerate(q_results["reference_sources_metadata"]):
                        source['matched'] = matches[idx]
                    logger.info(f"Current relative accuracy: {relative_source_accuracy / question_id if question_id > 0 else 0.0}")
                    logger.info(f"Current strict accuracy: {source_accuracy / question_id if question_id > 0 else 0.0}")

                # store the full source documents — metadata and complete content
                sources_metadata: List[Dict[str, Any]] = []
                sources_content: List[str] = []
                for document in result.get('source_documents', []):
                    metadata = getattr(document, 'metadata', {}) or {}
                    sources_metadata.append(metadata)
                    sources_content.append(getattr(document, 'page_content', ''))
                q_results['sources_metadata'] = sources_metadata
                q_results['sources_content'] = sources_content
                # backward compat alias used by run_evaluation.py, prepare_judge_batches.py, etc.
                q_results['sources_trunc_content'] = [s[:300] for s in sources_content]
                logger.debug("Sources returned: %s", sources_metadata)

                # store the results for this question
                question_wise_results[f"question_{question_id}"] = q_results

                # Save in-progress checkpoint after each question
                self._save_in_progress_checkpoint(
                    checkpoint_path, config_path, question_wise_results,
                    question_id, ragas_input, source_accuracy, relative_source_accuracy,
                )
                
                logger.info("====================================")
                logger.info("")

            if "RAGAS" in modes_being_run:
                logger.info(f"Starting to collect RAGAS results")
                data = Dataset.from_list(ragas_input)
                # were modifying final_addition here to add ragas results by question
                ragas_results = self.get_ragas_results(data, question_wise_results)

                for metric_name in ragas_results.columns:
                    total_results[f'aggregate_{metric_name}'] = ragas_results[metric_name].mean()

            if "SOURCES" in modes_being_run:
                total_results['relative_source_accuracy'] = relative_source_accuracy / len(self.queries_to_answers)
                total_results['source_accuracy'] = source_accuracy / len(self.queries_to_answers)

            if "LLM_JUDGE" in modes_being_run:
                logger.info("Starting LLM-as-Judge absolute scoring")
                llm_judge_df, llm_judge_usage = self.get_llm_judge_results(question_wise_results)
                for dim in llm_judge_df.columns:
                    total_results[f"aggregate_llm_judge_{dim}"] = llm_judge_df[dim].mean()
                total_results["llm_judge_tokens"] = llm_judge_usage
                any_llm_judge = True

            # Aggregate inference token usage across all questions
            inference_prompt = 0
            inference_completion = 0
            inference_counted = 0
            total_tool_calls = 0
            total_tool_time_ms = 0
            total_thinking_time_ms = 0
            questions_with_trace = 0
            for qr in question_wise_results.values():
                tu = qr.get("token_usage")
                if tu:
                    inference_prompt += tu.get("prompt_tokens", 0)
                    inference_completion += tu.get("completion_tokens", 0)
                    inference_counted += 1
                ts = qr.get("trace_summary")
                if ts:
                    total_tool_calls += ts.get("total_tool_calls", 0)
                    total_tool_time_ms += ts.get("total_tool_time_ms", 0)
                    total_thinking_time_ms += ts.get("total_thinking_time_ms", 0)
                    questions_with_trace += 1
            if inference_counted > 0:
                total_results["inference_token_usage"] = {
                    "prompt_tokens": inference_prompt,
                    "completion_tokens": inference_completion,
                    "total_tokens": inference_prompt + inference_completion,
                    "questions_with_usage": inference_counted,
                }
            if questions_with_trace > 0:
                total_results["aggregate_trace"] = {
                    "total_tool_calls": total_tool_calls,
                    "total_tool_time_ms": total_tool_time_ms,
                    "total_thinking_time_ms": total_thinking_time_ms,
                    "questions_with_trace": questions_with_trace,
                }

            ResultHandler.handle_results(Path(self.current_config), question_wise_results, total_results)

            # Save checkpoint after each config
            self._save_checkpoint(checkpoint_path, config_path, configs_to_run)

        ResultHandler.add_metadata()

        # A/B comparison: pair results and generate comparison output
        if self.ab_mode and len(_result_handler.results) >= 2:
            pairs = ResultHandler.generate_pairwise_combinations(len(_result_handler.results))
            logger.info("Generating %d pairwise A/B comparisons...", len(pairs))
            for idx_a, idx_b in pairs:
                paired = ResultHandler.pair_ab_results(idx_a, idx_b)

                # LLM-as-judge pairwise comparison with position swap
                if any_llm_judge:
                    llm_judge_settings = self._get_llm_judge_settings()
                    if llm_judge_settings.get("pairwise", True):
                        logger.info("Running LLM-as-Judge pairwise comparison for configs %d vs %d", idx_a, idx_b)
                        self.get_llm_judge_pairwise(paired)

                ResultHandler.dump_ab_comparison(paired, idx_a, idx_b)
                comp = _result_handler.ab_comparisons[-1]
                name_a = comp["config_a"].get("name", f"config_{idx_a}")
                name_b = comp["config_b"].get("name", f"config_{idx_b}")
                logger.info(
                    "  %s vs %s: %d questions. Wins A=%d, B=%d, Ties=%d",
                    name_a, name_b, len(paired),
                    comp["aggregate"]["wins_a"],
                    comp["aggregate"]["wins_b"],
                    comp["aggregate"]["ties"],
                )

        # Push results to Argilla if enabled
        if argilla_enabled:
            try:
                from src.utils.benchmark_argilla import (
                    generate_dataset_name,
                    push_ab_results_to_argilla,
                    push_single_results_to_argilla,
                    push_multi_ab_results_to_argilla,
                    write_state_file,
                )

                if _result_handler.ab_comparisons and len(_result_handler.ab_comparisons) > 1:
                    # Multi-pair: push each pair as a separate dataset
                    dataset_names = push_multi_ab_results_to_argilla(
                        _result_handler.ab_comparisons,
                        self.benchmark_name,
                    )
                    write_state_file(
                        dataset_name=dataset_names[0] if dataset_names else "",
                        dataset_names=dataset_names,
                    )
                    _result_handler.metadata["argilla_datasets"] = dataset_names
                    logger.info(
                        "Argilla export complete. %d datasets created. "
                        "Open Argilla to grade: archi grade --serve",
                        len(dataset_names),
                    )
                elif _result_handler.ab_comparison:
                    argilla_dataset_name = generate_dataset_name(self.benchmark_name)
                    benchmark_output = {
                        "benchmarking_results": _result_handler.results,
                        "ab_comparison": _result_handler.ab_comparison,
                    }
                    push_ab_results_to_argilla(benchmark_output, argilla_dataset_name)
                    write_state_file(argilla_dataset_name)
                    _result_handler.metadata["argilla_dataset"] = argilla_dataset_name
                    logger.info(
                        "Argilla export complete. Dataset: '%s'. "
                        "Open Argilla to grade: archi grade --serve",
                        argilla_dataset_name,
                    )
                else:
                    argilla_dataset_name = generate_dataset_name(self.benchmark_name)
                    benchmark_output = {
                        "benchmarking_results": _result_handler.results,
                    }
                    push_single_results_to_argilla(benchmark_output, argilla_dataset_name)
                    write_state_file(argilla_dataset_name)
                    _result_handler.metadata["argilla_dataset"] = argilla_dataset_name
                    logger.info(
                        "Argilla export complete. Dataset: '%s'. "
                        "Open Argilla to grade: archi grade --serve",
                        argilla_dataset_name,
                    )
            except ImportError:
                logger.error(
                    "Argilla export requested but 'argilla' package is not installed. "
                    "Install it with: pip install 'argilla>=2.5,<3'"
                )
            except Exception:
                logger.exception("Failed to push results to Argilla.")

        ResultHandler.dump(self.benchmark_name)

        # Finalize checkpoint
        self._finalize_checkpoint(checkpoint_path, configs_to_run)
        return

    def _load_checkpoint(self, checkpoint_path: Path) -> Dict[str, Any]:
        """Load checkpoint file if it exists."""
        try:
            with open(checkpoint_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_checkpoint(self, checkpoint_path: Path, completed_config: str, all_configs: List[str]):
        """Save checkpoint after a config completes."""
        existing = self._load_checkpoint(checkpoint_path) if checkpoint_path.exists() else {}
        completed = existing.get("completed_configs", [])
        if completed_config not in completed:
            completed.append(completed_config)
        os.makedirs(checkpoint_path.parent, exist_ok=True)
        checkpoint = {
            "benchmark_name": self.benchmark_name,
            "all_configs": all_configs,
            "completed_configs": completed,
            "results": _result_handler.results,
            "complete": len(completed) >= len(all_configs),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        checkpoint.pop("in_progress", None)  # clear partial progress on config completion
        with open(checkpoint_path, "w") as f:
            json.dump(checkpoint, f, indent=2, default=str)
        logger.info("Checkpoint saved: %d/%d configs complete.", len(completed), len(all_configs))

    def _save_in_progress_checkpoint(self, checkpoint_path: Path,
                                     config_path: str,
                                     question_wise_results: Dict[str, Any],
                                     question_id: int,
                                     ragas_input: list,
                                     source_accuracy: float,
                                     relative_source_accuracy: float):
        """Save per-question checkpoint so we can resume mid-config."""
        existing = self._load_checkpoint(checkpoint_path) if checkpoint_path.exists() else {}
        existing["in_progress"] = {
            "config_path": config_path,
            "question_wise_results": question_wise_results,
            "question_id": question_id,
            "ragas_input": ragas_input,
            "source_accuracy": source_accuracy,
            "relative_source_accuracy": relative_source_accuracy,
        }
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()
        os.makedirs(checkpoint_path.parent, exist_ok=True)
        with open(checkpoint_path, "w") as f:
            json.dump(existing, f, indent=2, default=str)

    def _finalize_checkpoint(self, checkpoint_path: Path, all_configs: List[str]):
        """Mark the checkpoint as fully complete."""
        if checkpoint_path.exists():
            checkpoint = self._load_checkpoint(checkpoint_path)
        else:
            checkpoint = {}
        checkpoint["complete"] = True
        checkpoint["completed_configs"] = all_configs
        checkpoint["results"] = _result_handler.results
        checkpoint["updated_at"] = datetime.now(timezone.utc).isoformat()
        os.makedirs(checkpoint_path.parent, exist_ok=True)
        with open(checkpoint_path, "w") as f:
            json.dump(checkpoint, f, indent=2, default=str)
        logger.info("Benchmark complete. Checkpoint finalized.")

    def wait_for_ingestion_completion(self):
        timeout_seconds = int(os.environ.get("BENCH_INGEST_WAIT_TIMEOUT", "3600"))
        poll_interval_seconds = int(os.environ.get("BENCH_INGEST_POLL_INTERVAL", "5"))
        dm_port = self.config.get("services", {}).get("data_manager", {}).get("external_port", 7871)
        status_urls = [
            f"http://data-manager:{dm_port}/api/ingestion/status",
            f"http://localhost:{dm_port}/api/ingestion/status",
            f"http://host.containers.internal:{dm_port}/api/ingestion/status",
        ]
        start_time = time.monotonic()
        attempt = 0

        logger.info("Waiting for data-manager ingestion to complete before benchmarking...")
        while True:
            attempt += 1
            last_error = None
            for status_url in status_urls:
                try:
                    with url_request.urlopen(status_url, timeout=5) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    state = str(payload.get("state", "")).lower()
                    step = payload.get("step")
                    err = payload.get("error")
                    logger.info(
                        "Ingestion status check #%s via %s -> state=%s step=%s",
                        attempt,
                        status_url,
                        state,
                        step,
                    )
                    if state == "completed":
                        logger.info("Data-manager ingestion completed; starting benchmark.")
                        print("[BENCH] Ingestion completed, starting benchmark", flush=True)
                        return
                    if state == "error":
                        raise RuntimeError(f"Data-manager ingestion failed at step '{step}': {err}")
                    break
                except (url_error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                    last_error = exc
                    continue

            elapsed = time.monotonic() - start_time
            if elapsed >= timeout_seconds:
                if last_error:
                    raise TimeoutError(
                        f"Timed out after {timeout_seconds}s waiting for ingestion status endpoint. Last error: {last_error}"
                    )
                raise TimeoutError(f"Timed out after {timeout_seconds}s waiting for ingestion completion.")

            time.sleep(poll_interval_seconds)

if __name__ == "__main__":

    query_file = Path("QandA.txt") 
    configs_folder = Path('configs')

    with open(Path(query_file), "r") as f:
        question_to_answer = json.load(f)

    benchmarker = Benchmarker(configs_folder, question_to_answer)
    benchmarker.run()
    logger.info("\n\nFINISHED RUNNING\n\n")
