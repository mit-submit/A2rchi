#!/usr/bin/env python3
"""
Analyze benchmark results — no LLM judge needed.

Computes structural metrics from the raw eval data:
  - Response time, answer length, tool usage, empty/error rates
  - Per-category breakdowns
  - Tool call distributions and tool repertoire analysis
  - Source retrieval patterns
  - Cross-config comparison tables
  - Cost estimate for GPT judge run

Usage:
    python scripts/analyze_results.py
    python scripts/analyze_results.py --input-dir bench_out/results/ --questions configs/submit76/curated_questions_categorized.json
"""

import argparse
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

# ── Config ordering for display ──
CONFIG_ORDER = [
    "bare-llm_gemma4-26b",
    "rag-only_gemma4-26b",
    "compops-no-tools_gemma4-26b",
    "copilot-no-tools_gemma4-26b",
    "compops_gemma4-26b",
    "compops_qwen3-32b",
    "compops_gpt-oss-120b",
    "copilot_gemma4-26b",
    "copilot_qwen3-32b",
    "copilot_gpt-oss-120b",
]

CONFIG_LABELS = {
    "bare-llm_gemma4-26b":         "BareLLM gem26b",
    "rag-only_gemma4-26b":         "RAG-only gem26b",
    "compops-no-tools_gemma4-26b": "CompOps-NT gem26b",
    "copilot-no-tools_gemma4-26b": "Copilot-NT gem26b",
    "compops_gemma4-26b":          "CompOps gem26b",
    "compops_qwen3-32b":           "CompOps qwen32b",
    "compops_gpt-oss-120b":        "CompOps gpt120b",
    "copilot_gemma4-26b":          "Copilot gem26b",
    "copilot_qwen3-32b":           "Copilot qwen32b",
    "copilot_gpt-oss-120b":        "Copilot gpt120b",
}


def load_results(input_dir: str) -> Dict[str, Dict]:
    """Load all result files, keyed by config name."""
    results = {}
    for fname in sorted(os.listdir(input_dir)):
        if not fname.endswith(".json"):
            continue
        name = fname.replace(".json", "")
        with open(os.path.join(input_dir, fname)) as f:
            data = json.load(f)
        cfg = data["benchmarking_results"][0]
        results[name] = cfg["single_question_results"]
    return results


def load_questions(path: str) -> Dict[str, Dict]:
    """Load curated questions keyed by question text."""
    with open(path) as f:
        data = json.load(f)
    return {q["question"].strip(): q for q in data}


# ── Metric extraction ──

def extract_metrics(sqr: Dict[str, Dict]) -> Dict[str, Any]:
    """Extract per-config metrics from single_question_results."""
    times = []
    ans_lens = []
    tool_counts = []
    tool_names = Counter()
    empty = 0
    n_sources = []

    for qd in sqr.values():
        t = qd.get("time_elapsed", 0)
        times.append(t)

        ans = qd.get("answer", "") or ""
        ans_lens.append(len(ans))
        if not ans.strip():
            empty += 1

        msgs = qd.get("messages", [])
        tc = sum(1 for m in msgs if m.get("tool_name"))
        tool_counts.append(tc)
        for m in msgs:
            tn = m.get("tool_name", "")
            if tn:
                tool_names[tn] += 1

        srcs = qd.get("sources_metadata", [])
        n_sources.append(len(srcs))

    return {
        "n": len(sqr),
        "times": times,
        "ans_lens": ans_lens,
        "tool_counts": tool_counts,
        "tool_names": tool_names,
        "empty": empty,
        "n_sources": n_sources,
    }


def fmt(val, decimals=1):
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "—"
    if isinstance(val, float):
        return f"{val:.{decimals}f}"
    return str(val)


def pct(num, denom):
    if denom == 0:
        return "—"
    return f"{num/denom*100:.1f}%"


def median(vals):
    return statistics.median(vals) if vals else 0


def p95(vals):
    if not vals:
        return 0
    s = sorted(vals)
    idx = int(len(s) * 0.95)
    return s[min(idx, len(s) - 1)]


# ── Reports ──

def print_overview(all_metrics: Dict[str, Dict]):
    """Print the main comparison table."""
    print("\n" + "=" * 130)
    print("OVERVIEW: All Configs")
    print("=" * 130)

    header = (
        f"{'Config':<22} {'N':>4} "
        f"{'Time μ':>7} {'Time md':>7} {'Time p95':>8} "
        f"{'AnsLen μ':>8} {'AnsLen md':>9} "
        f"{'Tools μ':>7} {'Tools md':>8} "
        f"{'Srcs μ':>6} "
        f"{'Empty':>6} {'Empty%':>7}"
    )
    print(header)
    print("-" * 130)

    for name in CONFIG_ORDER:
        if name not in all_metrics:
            continue
        m = all_metrics[name]
        label = CONFIG_LABELS.get(name, name)
        print(
            f"{label:<22} {m['n']:>4} "
            f"{fmt(statistics.mean(m['times'])):>7} {fmt(median(m['times'])):>7} {fmt(p95(m['times'])):>8} "
            f"{fmt(statistics.mean(m['ans_lens']),0):>8} {fmt(median(m['ans_lens']),0):>9} "
            f"{fmt(statistics.mean(m['tool_counts'])):>7} {fmt(median(m['tool_counts']),0):>8} "
            f"{fmt(statistics.mean(m['n_sources'])):>6} "
            f"{m['empty']:>6} {pct(m['empty'], m['n']):>7}"
        )
    print("=" * 130)


def print_tool_usage(all_metrics: Dict[str, Dict]):
    """Print tool usage breakdown per config."""
    print("\n" + "=" * 100)
    print("TOOL USAGE BREAKDOWN")
    print("=" * 100)

    # Group tools by category
    tool_categories = {
        "RAG/Search": ["search_vectorstore", "search_vectorstore_hybrid", "search", "search_metadata",
                        "search_metadata_index", "search_local_files", "metadata_index",
                        "list_metadata_schema", "search_opensearch_aggregation"],
        "Document Fetch": ["fetch_catalog_document", "fetch_document", "fetch_ticket",
                           "fetch_file", "fetch", "open_file", "repo_browser.open_file", "view_range"],
        "MONIT/Condor/Rucio": ["monit_opensearch_search", "monit_opensearch_aggregation",
                          "condor_opensearch_search", "condor_opensearch_aggregation",
                          "condor_metric_search", "condor_metric_aggregation",
                          "rucio_events_search", "rucio_events_aggregation"],
        "Other": ["exec", "container.exec"],
    }

    # Classify remaining tools
    classified = set()
    for tools in tool_categories.values():
        classified.update(tools)
    all_tools = set()
    for m in all_metrics.values():
        all_tools.update(m["tool_names"].keys())
    unclassified = all_tools - classified
    if unclassified:
        tool_categories["Unclassified"] = sorted(unclassified)

    active_configs = [n for n in CONFIG_ORDER if n in all_metrics]

    for cat_name, cat_tools in tool_categories.items():
        has_data = any(
            m["tool_names"].get(t, 0) > 0
            for m in all_metrics.values()
            for t in cat_tools
        )
        if not has_data:
            continue

        print(f"\n  {cat_name}:")
        print(f"    {'Tool':<35}", end="")
        for name in active_configs:
            label = CONFIG_LABELS.get(name, name)[:12]
            print(f" {label:>12}", end="")
        print()
        print("    " + "-" * (35 + 13 * len(active_configs)))

        for tool in sorted(cat_tools):
            has_any = any(all_metrics[n]["tool_names"].get(tool, 0) for n in active_configs)
            if not has_any:
                continue
            print(f"    {tool:<35}", end="")
            for name in active_configs:
                cnt = all_metrics[name]["tool_names"].get(tool, 0)
                print(f" {cnt:>12}", end="")
            print()


def print_tool_repertoire(all_metrics: Dict[str, Dict]):
    """Show how many unique tools each config uses."""
    print("\n" + "=" * 90)
    print("TOOL REPERTOIRE (unique tools used)")
    print("=" * 90)
    for name in CONFIG_ORDER:
        if name not in all_metrics:
            continue
        m = all_metrics[name]
        label = CONFIG_LABELS.get(name, name)
        total_calls = sum(m["tool_names"].values())
        unique = len(m["tool_names"])
        top3 = m["tool_names"].most_common(3)
        top3_str = ", ".join(f"{t}({c})" for t, c in top3)
        print(f"  {label:<22} {unique:>3} unique, {total_calls:>6} total  |  top: {top3_str}")


def print_category_breakdown(results: Dict[str, Dict], questions: Dict[str, Dict]):
    """Break down metrics by question category."""
    print("\n" + "=" * 130)
    print("PER-CATEGORY BREAKDOWN")
    print("=" * 130)

    categories = sorted(set(q.get("category", "unknown") for q in questions.values()))

    for cat in categories:
        print(f"\n  Category: {cat}")
        cat_qs = {q_text for q_text, q in questions.items() if q.get("category") == cat}
        print(f"  Questions: {len(cat_qs)}")

        header = f"    {'Config':<22} {'N':>4} {'Time μ':>7} {'AnsLen μ':>8} {'Tools μ':>7} {'Empty':>6} {'Empty%':>7}"
        print(header)
        print("    " + "-" * 65)

        for name in CONFIG_ORDER:
            if name not in results:
                continue
            sqr = results[name]
            label = CONFIG_LABELS.get(name, name)

            cat_data = []
            for qk, qd in sqr.items():
                q_text = qd["question"].strip()
                if q_text in cat_qs:
                    cat_data.append(qd)

            if not cat_data:
                continue

            times = [qd.get("time_elapsed", 0) for qd in cat_data]
            ans_lens = [len(qd.get("answer", "") or "") for qd in cat_data]
            tool_counts = [sum(1 for m in qd.get("messages", []) if m.get("tool_name")) for qd in cat_data]
            empty = sum(1 for qd in cat_data if not (qd.get("answer", "") or "").strip())

            print(
                f"    {label:<22} {len(cat_data):>4} "
                f"{fmt(statistics.mean(times)):>7} "
                f"{fmt(statistics.mean(ans_lens),0):>8} "
                f"{fmt(statistics.mean(tool_counts)):>7} "
                f"{empty:>6} {pct(empty, len(cat_data)):>7}"
            )


def print_answer_length_distribution(all_metrics: Dict[str, Dict]):
    """Show answer length distribution buckets."""
    print("\n" + "=" * 110)
    print("ANSWER LENGTH DISTRIBUTION")
    print("=" * 110)

    buckets = [(0, 0, "empty"), (1, 200, "1-200"), (201, 500, "201-500"),
               (501, 1000, "501-1K"), (1001, 2000, "1K-2K"), (2001, 5000, "2K-5K"),
               (5001, 99999, "5K+")]

    header = f"  {'Config':<22}"
    for _, _, label in buckets:
        header += f" {label:>8}"
    print(header)
    print("  " + "-" * (22 + 9 * len(buckets)))

    for name in CONFIG_ORDER:
        if name not in all_metrics:
            continue
        m = all_metrics[name]
        label = CONFIG_LABELS.get(name, name)
        lens = m["ans_lens"]
        row = f"  {label:<22}"
        for lo, hi, _ in buckets:
            cnt = sum(1 for l in lens if lo <= l <= hi)
            row += f" {cnt:>8}"
        print(row)


def print_time_distribution(all_metrics: Dict[str, Dict]):
    """Show response time distribution."""
    print("\n" + "=" * 120)
    print("RESPONSE TIME DISTRIBUTION (seconds)")
    print("=" * 120)

    buckets = [(0, 5, "<5s"), (5, 10, "5-10s"), (10, 20, "10-20s"),
               (20, 60, "20-60s"), (60, 120, "1-2m"), (120, 300, "2-5m"),
               (300, 99999, "5m+")]

    header = f"  {'Config':<22}"
    for _, _, label in buckets:
        header += f" {label:>8}"
    print(header)
    print("  " + "-" * (22 + 9 * len(buckets)))

    for name in CONFIG_ORDER:
        if name not in all_metrics:
            continue
        m = all_metrics[name]
        label = CONFIG_LABELS.get(name, name)
        times = m["times"]
        row = f"  {label:<22}"
        for lo, hi, _ in buckets:
            cnt = sum(1 for t in times if lo <= t < hi)
            row += f" {cnt:>8}"
        print(row)


def print_source_analysis(results: Dict[str, Dict]):
    """Analyze source retrieval patterns."""
    print("\n" + "=" * 100)
    print("SOURCE RETRIEVAL ANALYSIS")
    print("=" * 100)

    for name in CONFIG_ORDER:
        if name not in results:
            continue
        sqr = results[name]
        label = CONFIG_LABELS.get(name, name)

        src_counts = Counter()
        total_srcs = 0
        qs_with_srcs = 0
        for qd in sqr.values():
            srcs = qd.get("sources_metadata", [])
            if srcs:
                qs_with_srcs += 1
            total_srcs += len(srcs)
            for s in srcs:
                path = s.get("source", s.get("path", "?"))
                if "jira" in path:
                    src_counts["jira"] += 1
                elif "git" in path:
                    src_counts["git"] += 1
                elif "website" in path:
                    src_counts["web"] += 1
                else:
                    src_counts["other"] += 1

        print(
            f"  {label:<22} "
            f"qs_with_sources={qs_with_srcs}/{len(sqr)}  "
            f"total={total_srcs}  "
            f"jira={src_counts['jira']}  git={src_counts['git']}  "
            f"web={src_counts['web']}  other={src_counts['other']}"
        )


def print_comparison_deltas(all_metrics: Dict[str, Dict]):
    """Show key comparison deltas for the paper."""
    print("\n" + "=" * 100)
    print("KEY COMPARISONS (for paper)")
    print("=" * 100)

    comparisons = [
        ("Effect of RAG (baseline → RAG)",
         "bare-llm_gemma4-26b", "rag-only_gemma4-26b"),
        ("Effect of Agent loop (RAG → CompOps no-tools)",
         "rag-only_gemma4-26b", "compops-no-tools_gemma4-26b"),
        ("Effect of Agent loop (RAG → Copilot no-tools)",
         "rag-only_gemma4-26b", "copilot-no-tools_gemma4-26b"),
        ("Effect of live tools (CompOps no-tools → CompOps)",
         "compops-no-tools_gemma4-26b", "compops_gemma4-26b"),
        ("Effect of live tools (Copilot no-tools → Copilot)",
         "copilot-no-tools_gemma4-26b", "copilot_gemma4-26b"),
        ("CompOps vs Copilot (no tools, gemma4)",
         "compops-no-tools_gemma4-26b", "copilot-no-tools_gemma4-26b"),
        ("CompOps vs Copilot (with tools, gemma4)",
         "compops_gemma4-26b", "copilot_gemma4-26b"),
        ("Model scaling: gemma4→qwen3 (CompOps)",
         "compops_gemma4-26b", "compops_qwen3-32b"),
        ("Model scaling: gemma4→gpt-oss (CompOps)",
         "compops_gemma4-26b", "compops_gpt-oss-120b"),
        ("Model scaling: gemma4→qwen3 (Copilot)",
         "copilot_gemma4-26b", "copilot_qwen3-32b"),
        ("Model scaling: gemma4→gpt-oss (Copilot)",
         "copilot_gemma4-26b", "copilot_gpt-oss-120b"),
    ]

    for desc, a, b in comparisons:
        if a not in all_metrics or b not in all_metrics:
            continue
        ma, mb = all_metrics[a], all_metrics[b]

        time_a, time_b = statistics.mean(ma["times"]), statistics.mean(mb["times"])
        len_a, len_b = statistics.mean(ma["ans_lens"]), statistics.mean(mb["ans_lens"])
        tool_a, tool_b = statistics.mean(ma["tool_counts"]), statistics.mean(mb["tool_counts"])
        empty_a, empty_b = ma["empty"], mb["empty"]

        label_a = CONFIG_LABELS.get(a, a)
        label_b = CONFIG_LABELS.get(b, b)

        print(f"\n  {desc}")
        print(f"    {label_a:<22} → {label_b:<22}")
        print(f"    Time:    {time_a:>7.1f}s → {time_b:>7.1f}s  ({time_b-time_a:>+.1f}s, {(time_b/time_a-1)*100:>+.0f}%)")
        print(f"    AnsLen:  {len_a:>7.0f}  → {len_b:>7.0f}   ({len_b-len_a:>+.0f}, {(len_b/max(len_a,1)-1)*100:>+.0f}%)")
        print(f"    Tools:   {tool_a:>7.1f}  → {tool_b:>7.1f}   ({tool_b-tool_a:>+.1f})")
        print(f"    Empty:   {empty_a:>7}  → {empty_b:>7}")


def print_error_patterns(results: Dict[str, Dict]):
    """Check for common error/refusal patterns in answers."""
    print("\n" + "=" * 100)
    print("ANSWER CONTENT PATTERNS")
    print("=" * 100)

    patterns = [
        ("refusal/hedge", ["i don't have access", "i cannot", "i'm unable", "i don't have enough",
                           "i'm not able", "i can't", "i do not have"]),
        ("ready/greeting", ["i'm ready to help", "how can i help", "hello!", "hi there"]),
        ("error mention", ["error", "exception", "traceback", "failed"]),
        ("apology", ["sorry", "apologize", "unfortunately"]),
        ("recursion/limit", ["recursion limit", "maximum", "limit reached"]),
    ]

    header = f"  {'Config':<22}"
    for label, _ in patterns:
        header += f" {label:>16}"
    print(header)
    print("  " + "-" * (22 + 17 * len(patterns)))

    for name in CONFIG_ORDER:
        if name not in results:
            continue
        sqr = results[name]
        label = CONFIG_LABELS.get(name, name)
        row = f"  {label:<22}"
        for _, keywords in patterns:
            cnt = sum(
                1 for qd in sqr.values()
                if any(kw in (qd.get("answer", "") or "").lower() for kw in keywords)
            )
            row += f" {cnt:>16}"
        print(row)


def estimate_judge_cost(results: Dict[str, Dict]):
    """Estimate GPT-4.1 judge API costs."""
    print("\n" + "=" * 100)
    print("GPT-4.1 JUDGE COST ESTIMATE")
    print("=" * 100)

    # GPT-4.1 pricing (April 2026):
    # Input: $2.00 / 1M tokens
    # Output: $8.00 / 1M tokens
    INPUT_PRICE_PER_M = 2.00
    OUTPUT_PRICE_PER_M = 8.00

    # Token estimation: ~4 chars per token (conservative)
    CHARS_PER_TOKEN = 4
    # Judge prompt template: rubric + instructions ~ 800 tokens
    PROMPT_OVERHEAD_TOKENS = 800
    # Judge output: reasoning + JSON ~ 300 tokens
    OUTPUT_TOKENS_PER_CALL = 300

    total_input_tokens = 0
    total_output_tokens = 0
    total_calls = 0
    total_skipped = 0

    print(f"\n  {'Config':<22} {'Calls':>6} {'Skip':>5} {'Avg Q tok':>9} {'Avg A tok':>9} {'Input tok':>10} {'Output tok':>11}")
    print("  " + "-" * 80)

    for name in CONFIG_ORDER:
        if name not in results:
            continue
        sqr = results[name]
        label = CONFIG_LABELS.get(name, name)

        n_calls = 0
        n_skip = 0
        q_tokens = 0
        a_tokens = 0
        config_input_tokens = 0

        for qd in sqr.values():
            ans = qd.get("answer", "") or ""
            if not ans.strip():
                n_skip += 1
                continue
            n_calls += 1
            q_tok = len(qd.get("question", "")) // CHARS_PER_TOKEN
            a_tok = len(ans) // CHARS_PER_TOKEN
            q_tokens += q_tok
            a_tokens += a_tok
            config_input_tokens += PROMPT_OVERHEAD_TOKENS + q_tok + a_tok

        config_output_tokens = n_calls * OUTPUT_TOKENS_PER_CALL
        total_input_tokens += config_input_tokens
        total_output_tokens += config_output_tokens
        total_calls += n_calls
        total_skipped += n_skip

        avg_q = q_tokens // max(n_calls, 1)
        avg_a = a_tokens // max(n_calls, 1)

        print(
            f"  {label:<22} {n_calls:>6} {n_skip:>5} {avg_q:>9} {avg_a:>9} "
            f"{config_input_tokens:>10,} {config_output_tokens:>11,}"
        )

    input_cost = total_input_tokens / 1_000_000 * INPUT_PRICE_PER_M
    output_cost = total_output_tokens / 1_000_000 * OUTPUT_PRICE_PER_M
    total_cost = input_cost + output_cost

    print("  " + "-" * 80)
    print(f"  {'TOTAL':<22} {total_calls:>6} {total_skipped:>5} {'':>9} {'':>9} {total_input_tokens:>10,} {total_output_tokens:>11,}")
    print()
    print(f"  Pricing (GPT-4.1):")
    print(f"    Input:  {total_input_tokens:>10,} tokens x ${INPUT_PRICE_PER_M}/M = ${input_cost:.2f}")
    print(f"    Output: {total_output_tokens:>10,} tokens x ${OUTPUT_PRICE_PER_M}/M = ${output_cost:.2f}")
    print(f"    {'─' * 45}")
    print(f"    ESTIMATED TOTAL: ${total_cost:.2f}")
    print(f"    (conservative — actual may be ±30%)")
    print()
    print(f"  Assumptions:")
    print(f"    - No reference answer injection")
    print(f"    - {CHARS_PER_TOKEN} chars/token (conservative)")
    print(f"    - {PROMPT_OVERHEAD_TOKENS} token overhead per call (rubric + instructions)")
    print(f"    - {OUTPUT_TOKENS_PER_CALL} output tokens per call (reasoning + JSON)")
    print(f"    - {total_skipped} empty answers skipped across all configs")


def main():
    parser = argparse.ArgumentParser(description="Analyze benchmark results (no LLM judge)")
    parser.add_argument("--input-dir", type=str, default="bench_out/results",
                        help="Directory with result JSON files")
    parser.add_argument("--questions", type=str,
                        default="configs/submit76/curated_questions_categorized.json",
                        help="Curated questions file with categories")
    parser.add_argument("--section", type=str, default="all",
                        choices=["all", "overview", "tools", "categories", "distributions",
                                 "sources", "comparisons", "patterns", "cost"],
                        help="Which analysis section to run")
    args = parser.parse_args()

    print(f"Loading results from {args.input_dir}...")
    results = load_results(args.input_dir)
    print(f"  Loaded {len(results)} configs: {', '.join(sorted(results.keys()))}")

    questions = load_questions(args.questions)
    print(f"  Loaded {len(questions)} categorized questions")

    # Extract metrics for each config
    all_metrics = {}
    for name, sqr in results.items():
        all_metrics[name] = extract_metrics(sqr)

    sections = args.section

    if sections in ("all", "overview"):
        print_overview(all_metrics)

    if sections in ("all", "distributions"):
        print_answer_length_distribution(all_metrics)
        print_time_distribution(all_metrics)

    if sections in ("all", "tools"):
        print_tool_usage(all_metrics)
        print_tool_repertoire(all_metrics)

    if sections in ("all", "sources"):
        print_source_analysis(results)

    if sections in ("all", "categories"):
        print_category_breakdown(results, questions)

    if sections in ("all", "comparisons"):
        print_comparison_deltas(all_metrics)

    if sections in ("all", "patterns"):
        print_error_patterns(results)

    if sections in ("all", "cost"):
        estimate_judge_cost(results)


if __name__ == "__main__":
    main()
