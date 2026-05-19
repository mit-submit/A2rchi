#!/usr/bin/env python3
"""Deep investigation: why are tools not helping?"""
import json
from collections import defaultdict, Counter

# Load data
cats_raw = json.load(open("configs/submit76/curated_questions_categorized.json"))
q_to_cat = {q["question"].strip(): q["category"] for q in cats_raw}

fixed = json.load(open("bench_out/judged/glm-5.1_glm-5.1_run1/compops-tools-fixed_gemma4-26b.json"))
no_tools = json.load(open("bench_out/judged/glm-5.1_run1/compops-no-tools_gemma4-26b.json"))

fixed_sqr = fixed["benchmarking_results"][0]["single_question_results"]
no_tools_sqr = no_tools["benchmarking_results"][0]["single_question_results"]

dims = ["relevance", "completeness", "specificity", "helpfulness"]

# ── 1. Per-question delta + tool usage ──
rows = []
for qk, qv in fixed_sqr.items():
    nt = no_tools_sqr.get(qk, {})
    cat = q_to_cat.get(qv["question"].strip(), "unknown")
    
    f_scores = {d: qv.get(f"llm_judge_{d}") or 0 for d in dims}
    nt_scores = {d: nt.get(f"llm_judge_{d}") or 0 for d in dims}
    f_sum = sum(f_scores.values())
    nt_sum = sum(nt_scores.values())
    delta = f_sum - nt_sum
    
    # Count tool calls and types
    tool_calls = []
    tool_errors = 0
    for m in qv.get("messages", []):
        if m.get("type") == "tool_call":
            tn = m.get("tool_name", "?")
            out = str(m.get("tool_output", ""))
            is_err = "error" in out.lower()[:150] or "Failed to resolve" in out
            tool_calls.append(tn)
            if is_err:
                tool_errors += 1
    
    rows.append({
        "qk": qk, "cat": cat, "question": qv["question"][:100],
        "f_sum": f_sum, "nt_sum": nt_sum, "delta": delta,
        "f_scores": f_scores, "nt_scores": nt_scores,
        "n_tools": len(tool_calls), "tool_errors": tool_errors,
        "tools_used": tool_calls,
        "answer_len_f": len(qv.get("answer", "")),
        "answer_len_nt": len(nt.get("answer", "")),
    })

rows.sort(key=lambda r: r["delta"])

# ── 2. Biggest losses (fixed < no-tools) ──
print("=" * 100)
print("TOP 15 BIGGEST LOSSES (fixed-tools << no-tools)")
print("=" * 100)
for r in rows[:15]:
    print(f"\n{r['qk']} [{r['cat']}]  delta={r['delta']:+d}  tools={r['n_tools']} (err={r['tool_errors']})")
    print(f"  Q: {r['question']}")
    print(f"  Fixed:    R={r['f_scores']['relevance']} C={r['f_scores']['completeness']} S={r['f_scores']['specificity']} H={r['f_scores']['helpfulness']}  (sum={r['f_sum']})")
    print(f"  No-tools: R={r['nt_scores']['relevance']} C={r['nt_scores']['completeness']} S={r['nt_scores']['specificity']} H={r['nt_scores']['helpfulness']}  (sum={r['nt_sum']})")
    print(f"  Tools: {Counter(r['tools_used']).most_common()}")
    print(f"  Answer len: fixed={r['answer_len_f']} no-tools={r['answer_len_nt']}")

# ── 3. Did using tools help? Segment by tool usage ──
print("\n" + "=" * 100)
print("SCORE COMPARISON BY TOOL USAGE")
print("=" * 100)

used_tools = [r for r in rows if r["n_tools"] > 0]
no_tool_use = [r for r in rows if r["n_tools"] == 0]

def avg_dim(rows_list, dim, run):
    key = f"{'f' if run == 'fixed' else 'nt'}_scores"
    vals = [r[key][dim] for r in rows_list]
    return sum(vals) / len(vals) if vals else 0

print(f"\n{'Segment':<35} {'N':>4}  {'F-Rel':>5} {'F-Cmp':>5} {'F-Spc':>5} {'F-Hlp':>5}  {'NT-Rel':>6} {'NT-Cmp':>6} {'NT-Spc':>6} {'NT-Hlp':>6}")
print("-" * 110)
for label, subset in [("Questions WITH tool calls", used_tools), ("Questions WITHOUT tool calls", no_tool_use)]:
    fr = " ".join(f"{avg_dim(subset, d, 'fixed'):>5.2f}" for d in dims)
    nr = " ".join(f"{avg_dim(subset, d, 'no_tools'):>6.2f}" for d in dims)
    print(f"{label:<35} {len(subset):>4}  {fr}  {nr}")

# ── 4. By tool type - do specific tools help or hurt? ──
print(f"\n{'Tool':<35} {'N_Qs':>5} {'Avg_delta':>10} {'Wins':>5} {'Losses':>7}")
print("-" * 70)
tool_to_qs = defaultdict(list)
for r in rows:
    seen = set()
    for t in r["tools_used"]:
        if t not in seen:
            tool_to_qs[t].append(r)
            seen.add(t)

for tool in sorted(tool_to_qs.keys(), key=lambda t: sum(r["delta"] for r in tool_to_qs[t]) / len(tool_to_qs[t])):
    qs = tool_to_qs[tool]
    avg_d = sum(r["delta"] for r in qs) / len(qs)
    wins = sum(1 for r in qs if r["delta"] > 0)
    losses = sum(1 for r in qs if r["delta"] < 0)
    print(f"{tool:<35} {len(qs):>5} {avg_d:>+10.2f} {wins:>5} {losses:>7}")

# ── 5. By category + tool usage ──
print("\n" + "=" * 100)
print("PER-CATEGORY: WITH TOOLS vs WITHOUT TOOLS (within fixed-tools run)")
print("=" * 100)
for cat in sorted(set(r["cat"] for r in rows)):
    cat_rows = [r for r in rows if r["cat"] == cat]
    with_t = [r for r in cat_rows if r["n_tools"] > 0]
    without_t = [r for r in cat_rows if r["n_tools"] == 0]
    if with_t and without_t:
        print(f"\n  {cat} (N={len(cat_rows)}: {len(with_t)} used tools, {len(without_t)} didn't)")
        for subset, label in [(with_t, "  used tools"), (without_t, "  no tools  ")]:
            avg_delta = sum(r["delta"] for r in subset) / len(subset)
            wins = sum(1 for r in subset if r["delta"] > 0)
            losses = sum(1 for r in subset if r["delta"] < 0)
            print(f"    {label}: avg_delta={avg_delta:>+6.2f}  W/L={wins}/{losses}")

# ── 6. Answer length analysis ──
print("\n" + "=" * 100)
print("ANSWER LENGTH ANALYSIS")
print("=" * 100)
for cat in sorted(set(r["cat"] for r in rows)):
    cat_rows = [r for r in rows if r["cat"] == cat]
    avg_f = sum(r["answer_len_f"] for r in cat_rows) / len(cat_rows)
    avg_nt = sum(r["answer_len_nt"] for r in cat_rows) / len(cat_rows)
    print(f"  {cat:<22} fixed={avg_f:>7.0f} chars  no-tools={avg_nt:>7.0f} chars  ratio={avg_f/avg_nt:.2f}x")
