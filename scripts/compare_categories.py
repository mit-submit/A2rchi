#!/usr/bin/env python3
"""Compare per-category scores across tools-fixed, no-tools, and tools-broken runs."""
import json
from collections import defaultdict

# Load categorized questions
cats = json.load(open("configs/submit76/curated_questions_categorized.json"))
q_to_cat = {}
for q in cats:
    q_to_cat[q["question"].strip()] = q["category"]

# Load judged results
fixed = json.load(open("bench_out/judged/glm-5.1_glm-5.1_run1/compops-tools-fixed_gemma4-26b.json"))
no_tools = json.load(open("bench_out/judged/glm-5.1_run1/compops-no-tools_gemma4-26b.json"))
broken = json.load(open("bench_out/judged/glm-5.1_run1/compops-tools_gemma4-26b.json"))

fixed_sqr = fixed["benchmarking_results"][0]["single_question_results"]
no_tools_sqr = no_tools["benchmarking_results"][0]["single_question_results"]
broken_sqr = broken["benchmarking_results"][0]["single_question_results"]

dims = ["relevance", "completeness", "specificity", "helpfulness"]

cat_scores = defaultdict(lambda: {d: {"fixed": [], "no_tools": [], "broken": []} for d in dims})

for qk, qv in fixed_sqr.items():
    q_text = qv["question"].strip()
    cat = q_to_cat.get(q_text, "unknown")
    nt_match = no_tools_sqr.get(qk, {})
    br_match = broken_sqr.get(qk, {})

    for d in dims:
        fv = qv.get(f"llm_judge_{d}")
        nv = nt_match.get(f"llm_judge_{d}")
        bv = br_match.get(f"llm_judge_{d}")
        if fv is not None:
            cat_scores[cat][d]["fixed"].append(fv)
        if nv is not None:
            cat_scores[cat][d]["no_tools"].append(nv)
        if bv is not None:
            cat_scores[cat][d]["broken"].append(bv)

def avg(lst):
    return sum(lst) / len(lst) if lst else 0

# Per-category table
header = f"{'Category':<22} {'N':>3}  {'Dimension':<14} {'Fixed':>6} {'NoTool':>6} {'Broken':>6}  {'Fix-NT':>7} {'Fix-Br':>7}"
print(header)
print("-" * len(header))
for cat in sorted(cat_scores.keys()):
    n = len(cat_scores[cat]["relevance"]["fixed"])
    first = True
    for d in dims:
        f = avg(cat_scores[cat][d]["fixed"])
        nt = avg(cat_scores[cat][d]["no_tools"])
        br = avg(cat_scores[cat][d]["broken"])
        delta_nt = f - nt
        delta_br = f - br
        label = cat if first else ""
        n_str = str(n) if first else ""
        print(f"{label:<22} {n_str:>3}  {d:<14} {f:>6.2f} {nt:>6.2f} {br:>6.2f}  {delta_nt:>+7.2f} {delta_br:>+7.2f}")
        first = False
    print()

# Win/loss per question (fixed vs no-tools)
print("\n=== Win/Tie/Loss: Fixed-tools vs No-tools (per question, sum of 4 dims) ===")
wins = ties = losses = 0
cat_wl = defaultdict(lambda: [0, 0, 0])  # win, tie, loss
for qk, qv in fixed_sqr.items():
    q_text = qv["question"].strip()
    cat = q_to_cat.get(q_text, "unknown")
    nt_match = no_tools_sqr.get(qk, {})
    f_sum = sum(qv.get(f"llm_judge_{d}", 0) or 0 for d in dims)
    nt_sum = sum(nt_match.get(f"llm_judge_{d}", 0) or 0 for d in dims)
    if f_sum > nt_sum:
        wins += 1
        cat_wl[cat][0] += 1
    elif f_sum == nt_sum:
        ties += 1
        cat_wl[cat][1] += 1
    else:
        losses += 1
        cat_wl[cat][2] += 1

print(f"\nOverall: {wins}W / {ties}T / {losses}L")
print(f"\n{'Category':<22} {'W':>4} {'T':>4} {'L':>4}  {'Win%':>5}")
print("-" * 45)
for cat in sorted(cat_wl.keys()):
    w, t, l = cat_wl[cat]
    total = w + t + l
    pct = w / total * 100 if total else 0
    print(f"{cat:<22} {w:>4} {t:>4} {l:>4}  {pct:>5.1f}%")

# Also fixed vs broken
print("\n=== Win/Tie/Loss: Fixed-tools vs Broken-tools ===")
wins2 = ties2 = losses2 = 0
cat_wl2 = defaultdict(lambda: [0, 0, 0])
for qk, qv in fixed_sqr.items():
    q_text = qv["question"].strip()
    cat = q_to_cat.get(q_text, "unknown")
    br_match = broken_sqr.get(qk, {})
    f_sum = sum(qv.get(f"llm_judge_{d}", 0) or 0 for d in dims)
    br_sum = sum(br_match.get(f"llm_judge_{d}", 0) or 0 for d in dims)
    if f_sum > br_sum:
        wins2 += 1
        cat_wl2[cat][0] += 1
    elif f_sum == br_sum:
        ties2 += 1
        cat_wl2[cat][1] += 1
    else:
        losses2 += 1
        cat_wl2[cat][2] += 1

print(f"\nOverall: {wins2}W / {ties2}T / {losses2}L")
print(f"\n{'Category':<22} {'W':>4} {'T':>4} {'L':>4}  {'Win%':>5}")
print("-" * 45)
for cat in sorted(cat_wl2.keys()):
    w, t, l = cat_wl2[cat]
    total = w + t + l
    pct = w / total * 100 if total else 0
    print(f"{cat:<22} {w:>4} {t:>4} {l:>4}  {pct:>5.1f}%")
