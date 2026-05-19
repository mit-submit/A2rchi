#!/usr/bin/env python3
"""Compare GPT-5 reference answers against system configs by category."""
import json
import sys
from collections import defaultdict

# Files to compare
FILES = {
    "GPT-5 Reference": "bench_out/judged/glm-5.1_glm-5.1_run1/gpt5-reference-answers.json",
    "Opt CompOps": "bench_out/judged/glm-5.1_glm-5.1_run1/optimized-tools_compops-gemma4-26b.json",
    "No-Tools CompOps": "bench_out/judged/glm-5.1_run1/compops-no-tools_gemma4-26b.json",
    "RAG Only": "bench_out/judged/glm-5.1_run1/rag-only_gemma4-26b.json",
    "Bare LLM": "bench_out/judged/glm-5.1_run1/bare-llm_gemma4-26b.json",
}

# Load category mappings by question text
with open("configs/submit76/curated_questions_categorized.json") as f:
    cats = json.load(f)
cat_map = {q["question"].strip(): q["category"] for q in cats}

DIMS = ["relevance", "completeness", "specificity", "helpfulness"]
DIM_FIELDS = {
    "relevance": "llm_judge_relevance",
    "completeness": "llm_judge_completeness",
    "specificity": "llm_judge_specificity",
    "helpfulness": "llm_judge_helpfulness",
}

def load_scores(path):
    with open(path) as f:
        data = json.load(f)
    config = data["benchmarking_results"][0]
    sqr = config["single_question_results"]  # dict keyed by question_N
    scores_by_cat = defaultdict(lambda: {d: [] for d in DIMS})
    matched = 0
    for qkey, q in sqr.items():
        qtxt = q.get("question", "").strip()
        cat = cat_map.get(qtxt, "unknown")
        for d in DIMS:
            v = q.get(DIM_FIELDS[d])
            if v is not None:
                scores_by_cat[cat][d].append(v)
        if cat != "unknown":
            matched += 1
    print(f"  {path}: matched {matched}/{len(sqr)} to categories")
    return scores_by_cat

def avg(vals):
    return sum(vals) / len(vals) if vals else 0.0

# Load all results
all_scores = {}
for label, path in FILES.items():
    try:
        all_scores[label] = load_scores(path)
    except FileNotFoundError:
        print(f"WARNING: {path} not found, skipping {label}")

categories = sorted(set(cat_map.values()))
labels = list(all_scores.keys())

# Print header
dim_short = {"relevance": "R", "completeness": "C", "specificity": "S", "helpfulness": "H"}
header_parts = ["Category".ljust(20)]
for label in labels:
    header_parts.append(f"{label:>18s}")
print(" | ".join(header_parts))
print("-" * len(" | ".join(header_parts)))

# Per dimension, per category
for dim in DIMS:
    print(f"\n=== {dim.upper()} ===")
    print(" | ".join(header_parts))
    print("-" * len(" | ".join(header_parts)))
    for cat in categories:
        row = [cat.ljust(20)]
        for label in labels:
            vals = all_scores[label].get(cat, {}).get(dim, [])
            row.append(f"{avg(vals):>18.2f}")
        print(" | ".join(row))
    # Overall
    row = ["OVERALL".ljust(20)]
    for label in labels:
        all_vals = []
        for cat in categories:
            all_vals.extend(all_scores[label].get(cat, {}).get(dim, []))
        row.append(f"{avg(all_vals):>18.2f}")
    print(" | ".join(row))

# Overall average across all dims
print(f"\n=== OVERALL AVERAGE (all dims) ===")
header2 = ["Category".ljust(20)]
for label in labels:
    header2.append(f"{label:>18s}")
print(" | ".join(header2))
print("-" * len(" | ".join(header2)))
for cat in categories:
    row = [cat.ljust(20)]
    for label in labels:
        all_dim_vals = []
        for dim in DIMS:
            all_dim_vals.extend(all_scores[label].get(cat, {}).get(dim, []))
        row.append(f"{avg(all_dim_vals):>18.2f}")
    print(" | ".join(row))
# Grand overall
row = ["OVERALL".ljust(20)]
for label in labels:
    all_vals = []
    for cat in categories:
        for dim in DIMS:
            all_vals.extend(all_scores[label].get(cat, {}).get(dim, []))
    row.append(f"{avg(all_vals):>18.2f}")
print(" | ".join(row))

# Also print a compact summary table
print(f"\n{'='*100}")
print("COMPACT SUMMARY: Average by Category (R/C/S/H/Avg)")
print(f"{'='*100}")
compact_header = ["Category".ljust(20)]
for label in labels:
    compact_header.append(f"{label:>22s}")
print(" | ".join(compact_header))
print("-" * len(" | ".join(compact_header)))

for cat in categories + ["OVERALL"]:
    row = [cat.ljust(20)]
    for label in labels:
        dim_avgs = []
        for dim in DIMS:
            if cat == "OVERALL":
                vals = []
                for c in categories:
                    vals.extend(all_scores[label].get(c, {}).get(dim, []))
            else:
                vals = all_scores[label].get(cat, {}).get(dim, [])
            dim_avgs.append(avg(vals))
        overall = sum(dim_avgs) / len(dim_avgs)
        s = f"{dim_avgs[0]:.1f}/{dim_avgs[1]:.1f}/{dim_avgs[2]:.1f}/{dim_avgs[3]:.1f}={overall:.2f}"
        row.append(f"{s:>22s}")
    print(" | ".join(row))
