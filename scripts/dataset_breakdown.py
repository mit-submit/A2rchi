#!/usr/bin/env python3
"""Print dataset breakdown tables for curated questions."""
import json
from collections import Counter

with open("configs/submit76/curated_questions_categorized.json") as f:
    qs = json.load(f)

print(f"Total questions: {len(qs)}\n")

# --- Answerable breakdown ---
ans = Counter(q.get("answerable_from_docs", "unknown") for q in qs)
print("=== Answerable from Docs ===")
for k, v in sorted(ans.items(), key=lambda x: -x[1]):
    label = "answerable" if k is True else ("live-access" if k is False else str(k))
    print(f"  {label:>12s}: {v:3d} ({100*v/len(qs):.1f}%)")

# --- Category breakdown ---
cats = Counter(q.get("category", "unknown") for q in qs)
print(f"\n=== Category Breakdown ({len(cats)} categories) ===")
for k, v in sorted(cats.items(), key=lambda x: -x[1]):
    print(f"  {k:>25s}: {v:3d} ({100*v/len(qs):.1f}%)")

# --- Category x Answerable crosstab ---
print("\n=== Category x Answerable Crosstab ===")
print(f"{'Category':>25s} {'Answerable':>10s} {'Live-Access':>11s} {'Total':>6s}")
print("-" * 56)
crosstab = {}
for q in qs:
    cat = q.get("category", "unknown")
    a = q.get("answerable_from_docs", "unknown")
    if cat not in crosstab:
        crosstab[cat] = {True: 0, False: 0}
    crosstab[cat][a] += 1
for cat in sorted(crosstab, key=lambda c: -(crosstab[c][True] + crosstab[c][False])):
    t = crosstab[cat][True]
    f_ = crosstab[cat][False]
    print(f"{cat:>25s} {t:10d} {f_:11d} {t+f_:6d}")
print("-" * 56)
print(
    f"{'TOTAL':>25s} "
    f"{sum(v[True] for v in crosstab.values()):10d} "
    f"{sum(v[False] for v in crosstab.values()):11d} "
    f"{len(qs):6d}"
)

# --- Multi-turn ---
mt = Counter(q.get("multi_turn", "unknown") for q in qs)
print("\n=== Multi-turn ===")
for k, v in sorted(mt.items(), key=lambda x: -x[1]):
    label = "yes" if k is True else ("no" if k is False else str(k))
    print(f"  {label:>10s}: {v:3d} ({100*v/len(qs):.1f}%)")

# --- Time-sensitive ---
ts = Counter(q.get("time_sensitive", "unknown") for q in qs)
print("\n=== Time-sensitive ===")
for k, v in sorted(ts.items(), key=lambda x: -x[1]):
    label = "yes" if k is True else ("no" if k is False else str(k))
    print(f"  {label:>10s}: {v:3d} ({100*v/len(qs):.1f}%)")

# --- Reference Answers ---
has_ref = sum(
    1
    for q in qs
    if q.get("reference_answer")
    and q["reference_answer"] != "N/A"
    and len(q["reference_answer"].strip()) > 0
)
n_ans = ans.get(True, 0)
n_live = ans.get(False, 0)
has_ref_ans = sum(
    1
    for q in qs
    if q.get("answerable_from_docs") is True
    and q.get("reference_answer")
    and q["reference_answer"] != "N/A"
    and len(q["reference_answer"].strip()) > 0
)
has_ref_live = sum(
    1
    for q in qs
    if q.get("answerable_from_docs") is False
    and q.get("reference_answer")
    and q["reference_answer"] != "N/A"
    and len(q["reference_answer"].strip()) > 0
)
print("\n=== Reference Answers ===")
print(f"  Has reference: {has_ref} ({100*has_ref/len(qs):.1f}%)")
print(f"  No reference:  {len(qs)-has_ref} ({100*(len(qs)-has_ref)/len(qs):.1f}%)")
print(f"  Answerable w/ ref: {has_ref_ans}/{n_ans} ({100*has_ref_ans/n_ans:.1f}%)")
print(f"  Live-access w/ ref: {has_ref_live}/{n_live} ({100*has_ref_live/n_live:.1f}%)")

# --- Reference source ---
rs = Counter(q.get("reference_source", "none") for q in qs)
print("\n=== Reference Source ===")
for k, v in sorted(rs.items(), key=lambda x: -x[1]):
    print(f"  {str(k):>20s}: {v:3d} ({100*v/len(qs):.1f}%)")

# --- Question length stats ---
lengths = [len(q["question"]) for q in qs]
lengths.sort()
print("\n=== Question Length (chars) ===")
print(f"  Min: {min(lengths)}, Max: {max(lengths)}, "
      f"Median: {lengths[len(lengths)//2]}, Mean: {sum(lengths)/len(lengths):.0f}")
