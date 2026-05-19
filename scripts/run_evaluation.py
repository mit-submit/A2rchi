#!/usr/bin/env python3
"""
Run LLM-as-Judge evaluation on benchmark results via OpenRouter.

Evaluates configs with frontier LLM judges using a reference-free rubric:
  - relevance, completeness, specificity, helpfulness (1-5 scale, all questions)
  - source_faithfulness (1-5, conditional on pipeline having sources)

Usage:
    export OPENROUTER_API_KEY='sk-or-...'

    # Evaluate all files in bench_out/results/ with GLM 5.1:
    python scripts/run_evaluation.py --input-dir bench_out/results/

    # Use a different judge model:
    python scripts/run_evaluation.py --input-dir bench_out/results/ --model google/gemini-3.1-pro-preview

    # Reliability run (second run with different run-id):
    python scripts/run_evaluation.py --input-dir bench_out/results/ --run-id run2

    # Evaluate a single file:
    python scripts/run_evaluation.py --input bench_out/results/compops_gemma4-26b.json

    # Force re-judge (ignore existing scores):
    python scripts/run_evaluation.py --input-dir bench_out/results/ --force-rejudge
"""

import argparse
import glob
import json
import math
import os
import signal
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

import openai

# ── Graceful shutdown ──────────────────────────────────────────────
_shutdown_requested = False

def _handle_signal(signum, frame):
    global _shutdown_requested
    if _shutdown_requested:
        print("\n  [SHUTDOWN] Second signal — forcing exit")
        sys.exit(1)
    _shutdown_requested = True
    print("\n  [SHUTDOWN] Graceful shutdown requested — finishing current questions, then saving...")

signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ── Configuration ──────────────────────────────────────────────────
INPUT_DIR = "bench_out/results"
OUTPUT_DIR = "bench_out/judged"
DEFAULT_JUDGE_MODEL = "z-ai/glm-5.1"
DEFAULT_RUN_ID = "run1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MAX_WORKERS = 20       # concurrent API calls for LLM Judge

# ── Cost per 1M tokens (input_rate, output_rate) in USD ──────────
COST_PER_M_TOKENS = {
    "z-ai/glm-5.1":                   (0.95, 3.15),
    "google/gemini-3.1-pro-preview":   (2.00, 12.00),
    "openai/gpt-5.4":                  (2.50, 15.00),
    "anthropic/claude-opus-4.6":       (5.00, 25.00),
}

def estimate_cost(model: str, usage: Dict[str, int]) -> float:
    """Estimate USD cost from token usage for a given model."""
    rates = COST_PER_M_TOKENS.get(model)
    if not rates:
        return 0.0
    input_rate, output_rate = rates
    prompt = usage.get("prompt_tokens", 0)
    completion = usage.get("completion_tokens", 0)
    return (prompt / 1_000_000) * input_rate + (completion / 1_000_000) * output_rate

# ── LLM Judge Rubrics (v4 — reference-free, uniform dimensions) ──
#
# All questions scored on the same dimensions. No dimension switching.
# Source faithfulness is only included when the pipeline has retrieval/tools.

RUBRIC_RELEVANCE = (
    "**Relevance** — Does the answer address the specific question that was asked?\n"
    "- 5: Directly and precisely addresses the question — every part of the response is on-topic\n"
    "- 4: Addresses the question with minor tangential content\n"
    "- 3: Partially addresses the question but includes significant off-topic material, "
    "or only addresses part of a multi-part question\n"
    "- 2: Mostly off-topic — touches on the general subject area but does not answer what was asked\n"
    "- 1: Completely irrelevant, or a non-response (\"I'm ready to help!\", empty, greeting-only)"
)

RUBRIC_COMPLETENESS = (
    "**Completeness** — How many aspects of the question does the answer address?\n"
    "Assess scope by inferring what a full answer would need to cover from the question itself. "
    "For multi-part questions, a complete answer addresses all parts.\n"
    "- 5: Addresses all aspects of the question — no significant gaps\n"
    "- 4: Addresses most aspects, one minor gap\n"
    "- 3: Addresses the core question but misses important context or sub-questions\n"
    "- 2: Only partially addresses the question — significant gaps\n"
    "- 1: Does not meaningfully address the question, or is a non-response"
)

RUBRIC_SPECIFICITY = (
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
)

RUBRIC_HELPFULNESS = (
    "**Helpfulness** — Would a CMS computing operator be able to make progress on their task using this answer?\n"
    "This is the bottom-line pragmatic dimension. An answer can be relevant, complete, and specific "
    "but still unhelpful if it points in the wrong direction.\n"
    "- 5: An operator could act on this answer immediately — clear, correct next steps with enough detail to execute\n"
    "- 4: Useful — provides a path forward, may require minor follow-up to fully act on\n"
    "- 3: Somewhat useful — gives the operator a starting point but requires significant additional investigation\n"
    "- 2: Minimally useful — vague pointers or a refusal with no alternative guidance\n"
    "- 1: Not useful or actively harmful — would send the operator in the wrong direction, or is a non-response"
)

RUBRIC_SOURCE_FAITHFULNESS = (
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
)

# All questions get the same base dimensions; source_faithfulness is conditional
BASE_DIMENSIONS = ["relevance", "completeness", "specificity", "helpfulness"]


# ── LLM Judge ─────────────────────────────────────────────────────

def get_dimensions(has_sources: bool) -> list:
    """Return the list of judge dimensions for this question."""
    dims = list(BASE_DIMENSIONS)
    if has_sources:
        dims.append("source_faithfulness")
    return dims


def build_judge_prompt(question: str, generated_answer: str,
                       has_sources: bool = False) -> str:
    dims = get_dimensions(has_sources)

    rubric_map = {
        "relevance": RUBRIC_RELEVANCE,
        "completeness": RUBRIC_COMPLETENESS,
        "specificity": RUBRIC_SPECIFICITY,
        "helpfulness": RUBRIC_HELPFULNESS,
        "source_faithfulness": RUBRIC_SOURCE_FAITHFULNESS,
    }
    rubric_parts = [rubric_map[d] for d in dims]
    rubric_text = "\n\n".join(rubric_parts)
    dim_keys = ", ".join(f'"{d}"' for d in dims)

    return (
        "You are an expert evaluator for a CMS Computing Operations AI assistant. "
        "Evaluate the generated answer on the following dimensions using a 1\u20135 scale.\n\n"
        "IMPORTANT PRINCIPLES:\n"
        "- This is a REFERENCE-FREE evaluation. Score based on the answer's own quality alone.\n"
        "- Unsupported specific claims (invented ticket numbers, dates, data values with no "
        "cited source) are WORSE than honest vagueness. An answer saying \"I don't have access "
        "to look that up\" is more trustworthy than one inventing data.\n"
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


def call_llm_judge(client: openai.OpenAI, question: str,
                    generated_answer: str, has_sources: bool = False,
                    model: str = DEFAULT_JUDGE_MODEL,
                    max_retries: int = 3) -> Dict[str, Any]:
    prompt = build_judge_prompt(question, generated_answer, has_sources)
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
            )
            content = response.choices[0].message.content
            if not content:
                raise ValueError(f"Empty response (finish_reason={response.choices[0].finish_reason})")
            usage = getattr(response, 'usage', None)
            result = json.loads(content)
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
                    wait = min(2 ** (attempt + 2), 30)  # longer backoff for rate limits
                time.sleep(wait)
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise last_err


def run_llm_judge(config_idx: int, config_name: str, sqr: Dict[str, Dict],
                  model: str = DEFAULT_JUDGE_MODEL,
                  retry_errors: bool = False) -> Dict[str, int]:
    """Run LLM Judge on all questions in a config, updating sqr in-place.

    Args:
        retry_errors: If True, re-judge questions that previously failed (ERROR: prefix).

    Returns dict with prompt_tokens + completion_tokens, or None if nothing to do.
    """
    client = openai.OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        timeout=120.0,
    )

    # Detect if this config has retrieval/tools (for groundedness)
    config_has_sources = any(v.get("sources_trunc_content") for v in sqr.values())

    # Filter to questions needing judging AND having an answer
    def needs_judging(v):
        if not v.get("answer"):
            return False
        if "llm_judge_relevance" not in v:
            return True  # never scored
        if retry_errors and isinstance(v.get("llm_judge_reasoning", ""), str) \
                and v.get("llm_judge_reasoning", "").startswith("ERROR:"):
            return True  # previously failed, retry requested
        return False

    to_judge = {k: v for k, v in sqr.items() if needs_judging(v)}

    if not to_judge:
        print(f"  [Judge] Config {config_idx} ({config_name}): all questions already scored, skipping")
        return

    total = len(to_judge)

    print(f"  [Judge] Config {config_idx} ({config_name}): scoring {total} questions "
          f"(sources={'yes' if config_has_sources else 'no'}) "
          f"with {MAX_WORKERS} workers, model={model}...")

    completed = 0
    failed = 0

    def judge_one(q_key: str, q_data: Dict) -> tuple:
        has_sources = bool(q_data.get("sources_trunc_content")) if config_has_sources else False
        try:
            scores = call_llm_judge(
                client,
                q_data["question"],
                q_data["answer"],
                has_sources=has_sources,
                model=model,
            )
            dims = get_dimensions(has_sources)
            return q_key, scores, None, dims
        except Exception as e:
            dims = get_dimensions(has_sources)
            return q_key, None, f"{type(e).__name__}: {str(e)[:200]}", dims

    all_usages = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(judge_one, k, v): k for k, v in to_judge.items()}

        for future in as_completed(futures):
            q_key, scores, error, dims = future.result()
            if error:
                failed += 1
                for dim in dims:
                    sqr[q_key][f"llm_judge_{dim}"] = None
                sqr[q_key]["llm_judge_reasoning"] = f"ERROR: {error}"
                if failed <= 3:
                    print(f"    [Judge] ERROR on {q_key}: {error}")
            else:
                # Collect token usage for post-completion aggregation
                usage = scores.pop("_usage", None)
                if usage:
                    all_usages.append(usage)

                for dim in dims:
                    raw = scores.get(dim)
                    sqr[q_key][f"llm_judge_{dim}"] = int(raw) if raw is not None else None
                sqr[q_key]["llm_judge_reasoning"] = scores.get("reasoning", "")

            completed += 1
            if completed % 10 == 0 or completed == total:
                print(f"    [Judge] {completed}/{total} done ({failed} failed)")

            # Check for graceful shutdown
            if _shutdown_requested:
                print(f"    [Judge] Shutdown requested — cancelling remaining futures")
                for f in futures:
                    f.cancel()
                break

    # Thread-safe: accumulate tokens after all futures complete
    total_prompt_tokens = sum(u.get("prompt_tokens", 0) for u in all_usages)
    total_completion_tokens = sum(u.get("completion_tokens", 0) for u in all_usages)

    print(f"  [Judge] Token usage: {total_prompt_tokens:,} prompt + {total_completion_tokens:,} completion = {total_prompt_tokens + total_completion_tokens:,} total")
    return {"prompt_tokens": total_prompt_tokens, "completion_tokens": total_completion_tokens}




# ── Reliability Check ──────────────────────────────────────────────

def run_reliability_check(data: Dict, n_samples: int, model: str = DEFAULT_JUDGE_MODEL) -> None:
    """Re-judge a random subset and report agreement with existing scores."""
    import random

    client = openai.OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=os.environ.get("OPENROUTER_API_KEY", ""),
        timeout=120.0,
    )

    # Use the first config for reliability check
    cfg = data["benchmarking_results"][0]
    sqr = cfg["single_question_results"]
    name = cfg["configuration_file"].split("/")[-1].replace(".yaml", "")

    # Find questions that have existing judge scores
    scored = {k: v for k, v in sqr.items()
              if "llm_judge_relevance" in v
              and v.get("answer")}

    if not scored:
        print("  [Reliability] No scored questions found — run evaluation first")
        return

    n = min(n_samples, len(scored))
    sample_keys = random.sample(list(scored.keys()), n)
    config_has_sources = any(v.get("sources_trunc_content") for v in sqr.values())

    print(f"\n{'=' * 60}")
    print(f"RELIABILITY CHECK: re-judging {n} questions from {name}")
    print(f"{'=' * 60}")

    deviations = {}  # dim -> list of |original - rejudge|
    for i, qk in enumerate(sample_keys):
        qd = scored[qk]
        has_sources = bool(qd.get("sources_trunc_content")) if config_has_sources else False
        dims = get_dimensions(has_sources)

        try:
            re_scores = call_llm_judge(
                client, qd["question"],
                qd["answer"], has_sources=has_sources, model=model,
            )
            for dim in dims:
                orig = qd.get(f"llm_judge_{dim}")
                new = re_scores.get(dim)
                if orig is not None and new is not None:
                    deviations.setdefault(dim, []).append(abs(int(orig) - int(new)))
        except Exception as e:
            print(f"    [Reliability] ERROR on {qk}: {e}")

        if (i + 1) % 5 == 0 or i + 1 == n:
            print(f"    [Reliability] {i + 1}/{n} re-judged")

    # Report
    print(f"\n{'=' * 60}")
    print("RELIABILITY RESULTS (Mean Absolute Deviation & Exact Agreement)")
    print(f"{'=' * 60}")
    print(f"{'Dimension':<25} {'N':<5} {'MAD':<8} {'Exact%':<8} {'±1%':<8}")
    print("-" * 54)
    for dim in sorted(deviations.keys()):
        devs = deviations[dim]
        nd = len(devs)
        mad = sum(devs) / nd
        exact = sum(1 for d in devs if d == 0) / nd * 100
        within1 = sum(1 for d in devs if d <= 1) / nd * 100
        print(f"{dim:<25} {nd:<5} {mad:<8.2f} {exact:<8.1f} {within1:<8.1f}")

    all_devs = [d for devs in deviations.values() for d in devs]
    if all_devs:
        total_mad = sum(all_devs) / len(all_devs)
        total_exact = sum(1 for d in all_devs if d == 0) / len(all_devs) * 100
        total_within1 = sum(1 for d in all_devs if d <= 1) / len(all_devs) * 100
        print("-" * 54)
        print(f"{'OVERALL':<25} {len(all_devs):<5} {total_mad:<8.2f} {total_exact:<8.1f} {total_within1:<8.1f}")


# ── Checkpointing & Reporting ─────────────────────────────────────

def save_checkpoint(data: Dict, config_idx: int, checkpoint_path: str) -> None:
    """Atomically write checkpoint (write to temp, then rename)."""
    dir_name = os.path.dirname(checkpoint_path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"last_completed_config": config_idx, "data": data}, f)
        os.replace(tmp_path, checkpoint_path)  # atomic on POSIX
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    print(f"  [Checkpoint] Saved after config {config_idx}")


def safe_mean(values: List) -> str:
    nums = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return f"{sum(nums) / len(nums):.3f}" if nums else "\u2014"


def safe_stats(values: List) -> tuple:
    """Return (mean, stddev, median, n) as formatted strings."""
    nums = [v for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
    if not nums:
        return "\u2014", "\u2014", "\u2014", "0"
    n = len(nums)
    mean = sum(nums) / n
    median = sorted(nums)[n // 2] if n % 2 == 1 else (sorted(nums)[n // 2 - 1] + sorted(nums)[n // 2]) / 2
    if n > 1:
        variance = sum((x - mean) ** 2 for x in nums) / (n - 1)
        stddev = variance ** 0.5
    else:
        stddev = 0.0
    return f"{mean:.2f}", f"{stddev:.2f}", f"{median:.1f}", str(n)


def print_summary(data: Dict) -> None:
    print("\n" + "=" * 120)
    print("EVALUATION SUMMARY")
    print("=" * 120)

    dims = ["relevance", "completeness", "specificity", "helpfulness", "source_faithfulness"]
    dim_labels = ["Relev", "Compl", "Spec", "Help", "SrcFaith"]

    # Header
    header_parts = [f"{'Config':<30} {'N':<5}"]
    for label in dim_labels:
        header_parts.append(f"{label+' μ':<7} {'σ':<5} {'med':<5}")
    header_parts.append(f"{'Err':<4}")
    print("  ".join(header_parts))
    print("-" * 120)

    for i, cfg in enumerate(data["benchmarking_results"]):
        sqr = cfg["single_question_results"]
        name = cfg.get("eval_name", cfg["configuration_file"].split("/")[-1].replace(".yaml", ""))
        n = len(sqr)

        errors = sum(1 for v in sqr.values()
                     if isinstance(v.get("llm_judge_reasoning", ""), str)
                     and v.get("llm_judge_reasoning", "").startswith("ERROR:"))

        row_parts = [f"{name:<30} {n:<5}"]
        for dim in dims:
            vals = [v.get(f"llm_judge_{dim}") for v in sqr.values()]
            mean_s, std_s, med_s, _ = safe_stats(vals)
            row_parts.append(f"{mean_s:<7} {std_s:<5} {med_s:<5}")
        row_parts.append(f"{errors:<4}")
        print("  ".join(row_parts))

    # Warn if configs have different question counts
    counts = set()
    for cfg in data["benchmarking_results"]:
        counts.add(len(cfg["single_question_results"]))
    if len(counts) > 1:
        print(f"\n  WARNING: configs have different question counts: {sorted(counts)}")
        print(f"    Cross-config comparisons should use matching question subsets.")

    print("=" * 120)


def print_grand_summary(all_data: List[Dict], model: str, grand_elapsed: float) -> None:
    """Print aggregate summary across all files."""
    total_prompt = 0
    total_completion = 0
    total_files = len(all_data)
    total_configs = 0
    total_errors = 0
    total_questions = 0

    for d in all_data:
        meta = d.get("judge_metadata", {})
        tokens = meta.get("tokens", {})
        total_prompt += tokens.get("prompt_tokens", 0)
        total_completion += tokens.get("completion_tokens", 0)
        for cfg in d["benchmarking_results"]:
            total_configs += 1
            sqr = cfg["single_question_results"]
            total_questions += len(sqr)
            total_errors += sum(
                1 for v in sqr.values()
                if isinstance(v.get("llm_judge_reasoning", ""), str)
                and v.get("llm_judge_reasoning", "").startswith("ERROR:")
            )

    total_tokens = total_prompt + total_completion
    usage = {"prompt_tokens": total_prompt, "completion_tokens": total_completion}
    cost = estimate_cost(model, usage)
    mins, secs = divmod(int(grand_elapsed), 60)

    print(f"\n{'=' * 70}")
    print("GRAND SUMMARY")
    print(f"{'=' * 70}")
    print(f"  Files:       {total_files}")
    print(f"  Configs:     {total_configs}")
    print(f"  Questions:   {total_questions}")
    print(f"  Errors:      {total_errors}")
    print(f"  Wall time:   {mins}m{secs:02d}s")
    print(f"  Tokens:      {total_prompt:,} prompt + {total_completion:,} completion = {total_tokens:,} total")
    if cost > 0:
        print(f"  Est. cost:   ${cost:.4f} ({model})")
    else:
        print(f"  Est. cost:   N/A (no pricing for {model})")
    print(f"{'=' * 70}")


# ── Helpers ─────────────────────────────────────────────────────────

def inject_references(data: Dict, ref_path: str) -> None:
    """Inject reference answers and metadata from curated questions file."""
    print(f"\nInjecting reference answers from {ref_path}...")
    with open(ref_path) as f:
        ref_data = json.load(f)
    ref_map = {}
    for item in ref_data:
        ref_map[item["question"].strip()] = {
            "reference_answer": item.get("reference_answer", "N/A"),
            "answerable_from_docs": item.get("answerable_from_docs", True),
        }
    injected_ref = 0
    injected_meta = 0
    total_q = 0
    for cfg in data["benchmarking_results"]:
        for qkey, qdata in cfg["single_question_results"].items():
            total_q += 1
            q = qdata["question"].strip()
            if q in ref_map:
                meta = ref_map[q]
                if meta["reference_answer"] not in ("N/A", "", None):
                    qdata["reference_answer"] = meta["reference_answer"]
                    injected_ref += 1
                qdata["answerable_from_docs"] = meta["answerable_from_docs"]
                injected_meta += 1
    print(f"  Injected {injected_ref}/{total_q} reference answers")
    print(f"  Injected {injected_meta}/{total_q} answerable_from_docs metadata")


def clear_judge_scores(data: Dict) -> None:
    """Clear existing judge scores from all questions."""
    print("\n--force-rejudge: clearing existing judge scores...")
    cleared = 0
    for cfg in data["benchmarking_results"]:
        for qdata in cfg["single_question_results"].values():
            judge_keys = [k for k in qdata if k.startswith("llm_judge_")]
            if judge_keys:
                for k in judge_keys:
                    del qdata[k]
                cleared += 1
    print(f"  Cleared judge scores from {cleared} questions")


def evaluate_data(data: Dict, args, output_path: str = None, checkpoint_path: str = None,
                  model: str = DEFAULT_JUDGE_MODEL, retry_errors: bool = False) -> Dict:
    """Run judge evaluation on a loaded data structure. Returns the scored data."""
    configs = data["benchmarking_results"]

    # Show current status
    for i, cfg in enumerate(configs):
        sqr = cfg["single_question_results"]
        name = cfg.get("eval_name", cfg["configuration_file"].split("/")[-1].replace(".yaml", ""))
        n = len(sqr)
        has_judge = sum(1 for v in sqr.values() if "llm_judge_relevance" in v)
        has_errors = sum(1 for v in sqr.values()
                        if isinstance(v.get("llm_judge_reasoning", ""), str)
                        and v.get("llm_judge_reasoning", "").startswith("ERROR:"))
        has_src = sum(1 for v in sqr.values() if v.get("sources_trunc_content"))
        status = "DONE" if has_judge == n and has_errors == 0 else "NEEDS EVAL"
        if has_errors:
            status = f"HAS {has_errors} ERRORS"
        print(f"  Config {i}: {name:<40} judge={has_judge}/{n}  errors={has_errors}  sources={has_src}/{n}  [{status}]")

    print(f"\nModel (Judge): {model}")
    print(f"LLM Judge parallelism: {MAX_WORKERS} workers\n")

    total_start = time.time()
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    for i, cfg in enumerate(configs):
        sqr = cfg["single_question_results"]
        name = cfg.get("eval_name", cfg["configuration_file"].split("/")[-1].replace(".yaml", ""))

        print(f"\n{'=' * 60}")
        print(f"CONFIG {i}: {name}")
        print(f"{'=' * 60}")

        t0 = time.time()
        usage = run_llm_judge(i, name, sqr, model=model, retry_errors=retry_errors)
        elapsed = time.time() - t0
        print(f"  Config {i} complete in {elapsed:.1f}s")

        if usage:
            total_usage["prompt_tokens"] += usage["prompt_tokens"]
            total_usage["completion_tokens"] += usage["completion_tokens"]

        if checkpoint_path:
            save_checkpoint(data, i, checkpoint_path)

        if _shutdown_requested:
            print(f"\n  [SHUTDOWN] Stopping after config {i} — progress saved to checkpoint")
            break

    total_elapsed = time.time() - total_start
    mins, secs = divmod(int(total_elapsed), 60)
    total_tokens = total_usage["prompt_tokens"] + total_usage["completion_tokens"]
    cost = estimate_cost(model, total_usage)
    print(f"\nTotal evaluation time: {mins}m{secs:02d}s")
    print(f"Total tokens: {total_usage['prompt_tokens']:,} prompt + {total_usage['completion_tokens']:,} completion = {total_tokens:,}")
    if cost > 0:
        print(f"Estimated cost: ${cost:.4f}")

    print_summary(data)
    return data, total_usage


# ── Main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run LLM-as-Judge evaluation via OpenRouter")
    parser.add_argument("--input-dir", type=str, default=None,
                        help="Directory of individual result JSON files (e.g. bench_out/results/)")
    parser.add_argument("--input", type=str, nargs="+", default=None,
                        help="One or more result JSON files to evaluate")
    parser.add_argument("--output-dir", type=str, default=OUTPUT_DIR,
                        help=f"Output directory for judged files (default: {OUTPUT_DIR})")
    parser.add_argument("--model", type=str, default=DEFAULT_JUDGE_MODEL,
                        help=f"Judge model via OpenRouter (default: {DEFAULT_JUDGE_MODEL})")
    parser.add_argument("--run-id", type=str, default=DEFAULT_RUN_ID,
                        help=f"Run identifier for reliability checks (default: {DEFAULT_RUN_ID})")
    parser.add_argument("--inject-references", type=str, default=None,
                        help="Path to curated_questions_categorized.json with reference_answer fields")
    parser.add_argument("--force-rejudge", action="store_true",
                        help="Clear existing judge scores and re-evaluate all questions")
    parser.add_argument("--retry-errors", action="store_true",
                        help="Re-judge only questions that previously failed (ERROR status)")
    parser.add_argument("--limit", type=int, default=0, metavar="N",
                        help="Process only the first N files (for testing)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be processed without making API calls")
    parser.add_argument("--reliability-check", type=int, default=0, metavar="N",
                        help="Re-judge N random questions from config 0 and report agreement")
    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY") and not args.dry_run:
        print("ERROR: OPENROUTER_API_KEY environment variable not set")
        sys.exit(1)

    # Sanitize model name for file paths (e.g. "z-ai/glm-5.1" -> "glm-5.1")
    model_slug = args.model.split("/")[-1]

    if not args.input_dir and not args.input:
        parser.error("--input-dir or --input is required")

    if args.input:
        input_files = args.input
    else:
        input_files = sorted(glob.glob(os.path.join(args.input_dir, "*.json")))
        if not input_files:
            print(f"ERROR: No JSON files found in {args.input_dir}")
            sys.exit(1)

    if args.limit > 0:
        input_files = input_files[:args.limit]
        print(f"  --limit {args.limit}: processing only {len(input_files)} file(s)")

    # Output dir includes judge model and run-id
    out_dir = os.path.join(args.output_dir, f"{model_slug}_{args.run_id}")
    os.makedirs(out_dir, exist_ok=True)
    print(f"Judge mode: {len(input_files)} files -> {out_dir}/")
    print(f"Model (Judge): {args.model}")
    print(f"Run ID: {args.run_id}")
    print(f"LLM Judge parallelism: {MAX_WORKERS} workers")
    if args.retry_errors:
        print(f"  --retry-errors: will re-judge previously failed questions")
    if args.dry_run:
        print(f"  --dry-run: showing status only, no API calls\n")
    else:
        print()

    all_data = []
    grand_start = time.time()

    for file_idx, fpath in enumerate(input_files):
        fname = os.path.basename(fpath)
        out_path = os.path.join(out_dir, fname)
        checkpoint_path = os.path.join(out_dir, f".checkpoint_{fname}")

        print(f"\n{'#' * 70}")
        print(f"FILE {file_idx + 1}/{len(input_files)}: {fname}")
        print(f"{'#' * 70}")

        # Load priority: checkpoint > existing output > source
        # This fixes the race: if output already exists and is newer than checkpoint, prefer it
        if os.path.exists(checkpoint_path) and not args.force_rejudge:
            if os.path.exists(out_path) and os.path.getmtime(out_path) > os.path.getmtime(checkpoint_path):
                print(f"  Output newer than checkpoint — loading output")
                with open(out_path) as f:
                    data = json.load(f)
            else:
                print(f"  Resuming from checkpoint...")
                with open(checkpoint_path) as f:
                    ckpt = json.load(f)
                data = ckpt["data"]
        elif os.path.exists(out_path) and not args.force_rejudge:
            print(f"  Loading existing output (for incremental scoring)...")
            with open(out_path) as f:
                data = json.load(f)
        else:
            with open(fpath) as f:
                data = json.load(f)

        # Tag each config with eval_name from filename
        eval_name = fname.replace(".json", "")
        for cfg in data["benchmarking_results"]:
            cfg["eval_name"] = eval_name

        if args.inject_references:
            inject_references(data, args.inject_references)

        if args.force_rejudge:
            clear_judge_scores(data)

        print(f"  Found {len(data['benchmarking_results'])} config(s)\n")

        if args.dry_run:
            # Show status without making any API calls
            for i, cfg in enumerate(data["benchmarking_results"]):
                sqr = cfg["single_question_results"]
                name = cfg.get("eval_name", eval_name)
                n = len(sqr)
                scored = sum(1 for v in sqr.values() if "llm_judge_relevance" in v)
                errors = sum(1 for v in sqr.values()
                             if isinstance(v.get("llm_judge_reasoning", ""), str)
                             and v.get("llm_judge_reasoning", "").startswith("ERROR:"))
                pending = n - scored
                if args.retry_errors:
                    pending += errors
                print(f"  Config {i}: {name:<40} scored={scored}/{n}  errors={errors}  pending={pending}")
            continue

        data, usage = evaluate_data(data, args, output_path=None,
                                    checkpoint_path=checkpoint_path, model=args.model,
                                    retry_errors=args.retry_errors)

        # Store judge + usage metadata and write once
        file_cost = estimate_cost(args.model, usage)
        data["judge_metadata"] = {
            "model": args.model,
            "run_id": args.run_id,
            "api": "openrouter",
            "tokens": usage,
            "estimated_cost_usd": round(file_cost, 6),
        }

        # Atomic write: temp file then rename (safe against crash)
        dir_name = os.path.dirname(out_path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp_path, out_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        print(f"  Saved {out_path}")

        all_data.append(data)

        # Clean up checkpoint on successful completion
        if os.path.exists(checkpoint_path):
            os.remove(checkpoint_path)

        if _shutdown_requested:
            print(f"\n  [SHUTDOWN] Stopping after file {file_idx + 1}/{len(input_files)}")
            break

    grand_elapsed = time.time() - grand_start
    mins, secs = divmod(int(grand_elapsed), 60)

    if args.dry_run:
        print(f"\n{'#' * 70}")
        print(f"DRY RUN COMPLETE — no API calls made")
        print(f"{'#' * 70}")
        return

    completed_files = len(all_data)
    total_files = len(input_files)
    status = "ALL FILES COMPLETE" if completed_files == total_files else f"{completed_files}/{total_files} FILES COMPLETE"
    if _shutdown_requested:
        status += " (interrupted)"

    print(f"\n{'#' * 70}")
    print(f"{status} — {mins}m{secs:02d}s total")
    print(f"{'#' * 70}")

    # Print combined summary
    combined = {"benchmarking_results": []}
    for d in all_data:
        combined["benchmarking_results"].extend(d["benchmarking_results"])
    print_summary(combined)

    # Print grand summary across all files (tokens, cost, errors)
    print_grand_summary(all_data, args.model, grand_elapsed)

    # Reliability check (optional)
    if args.reliability_check > 0:
        run_reliability_check(combined, args.reliability_check, model=args.model)


if __name__ == "__main__":
    main()
