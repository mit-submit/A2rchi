#!/usr/bin/env python3
"""Compile Claude Opus 4.6 cross-config evaluation scores."""
import json
import statistics

all_items = [
    # item 0: conmon
    {"bare-llm-120b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"bare-llm-32b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"copilot-gpt-oss-120b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"copilot-qwen3-32b":{"correctness":3,"completeness":3,"relevance":4,"helpfulness":3},"rag-only-120b":{"correctness":3,"completeness":1,"relevance":2,"helpfulness":2},"rag-only-32b":{"correctness":3,"completeness":2,"relevance":3,"helpfulness":2}},
    # item 1: sandbox wrapper
    {"bare-llm-120b":{"correctness":2,"completeness":2,"relevance":3,"helpfulness":2},"bare-llm-32b":{"correctness":2,"completeness":2,"relevance":3,"helpfulness":2},"copilot-gpt-oss-120b":{"correctness":3,"completeness":3,"relevance":4,"helpfulness":3},"copilot-qwen3-32b":{"correctness":3,"completeness":4,"relevance":4,"helpfulness":4},"rag-only-120b":{"correctness":3,"completeness":3,"relevance":4,"helpfulness":3},"rag-only-32b":{"correctness":3,"completeness":3,"relevance":4,"helpfulness":3}},
    # item 2: check page
    {"bare-llm-120b":{"correctness":3,"completeness":2,"relevance":3,"helpfulness":3},"bare-llm-32b":{"correctness":3,"completeness":2,"relevance":3,"helpfulness":3},"copilot-gpt-oss-120b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"copilot-qwen3-32b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"rag-only-120b":{"correctness":4,"completeness":2,"relevance":3,"helpfulness":3},"rag-only-32b":{"correctness":3,"completeness":3,"relevance":3,"helpfulness":3}},
    # item 3: site readiness
    {"bare-llm-120b":{"correctness":1,"completeness":1,"relevance":2,"helpfulness":1},"bare-llm-32b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"copilot-gpt-oss-120b":{"correctness":5,"completeness":4,"relevance":5,"helpfulness":5},"copilot-qwen3-32b":{"correctness":4,"completeness":4,"relevance":5,"helpfulness":4},"rag-only-120b":{"correctness":5,"completeness":4,"relevance":5,"helpfulness":5},"rag-only-32b":{"correctness":2,"completeness":2,"relevance":2,"helpfulness":2}},
    # item 4: summarize google doc
    {"bare-llm-120b":{"correctness":4,"completeness":2,"relevance":3,"helpfulness":3},"bare-llm-32b":{"correctness":4,"completeness":2,"relevance":3,"helpfulness":3},"copilot-gpt-oss-120b":{"correctness":4,"completeness":3,"relevance":4,"helpfulness":4},"copilot-qwen3-32b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"rag-only-120b":{"correctness":4,"completeness":3,"relevance":3,"helpfulness":4},"rag-only-32b":{"correctness":4,"completeness":2,"relevance":3,"helpfulness":3}},
    # item 5: Tier0 release
    {"bare-llm-120b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"bare-llm-32b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"copilot-gpt-oss-120b":{"correctness":5,"completeness":4,"relevance":5,"helpfulness":5},"copilot-qwen3-32b":{"correctness":5,"completeness":4,"relevance":5,"helpfulness":5},"rag-only-120b":{"correctness":5,"completeness":5,"relevance":5,"helpfulness":5},"rag-only-32b":{"correctness":5,"completeness":4,"relevance":5,"helpfulness":5}},
    # item 6: Unified source
    {"bare-llm-120b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"bare-llm-32b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"copilot-gpt-oss-120b":{"correctness":1,"completeness":1,"relevance":2,"helpfulness":1},"copilot-qwen3-32b":{"correctness":1,"completeness":1,"relevance":2,"helpfulness":1},"rag-only-120b":{"correctness":2,"completeness":1,"relevance":2,"helpfulness":1},"rag-only-32b":{"correctness":1,"completeness":1,"relevance":2,"helpfulness":1}},
    # item 7: rucio rule add
    {"bare-llm-120b":{"correctness":4,"completeness":4,"relevance":5,"helpfulness":4},"bare-llm-32b":{"correctness":4,"completeness":4,"relevance":5,"helpfulness":4},"copilot-gpt-oss-120b":{"correctness":5,"completeness":5,"relevance":5,"helpfulness":5},"copilot-qwen3-32b":{"correctness":3,"completeness":2,"relevance":4,"helpfulness":3},"rag-only-120b":{"correctness":4,"completeness":4,"relevance":5,"helpfulness":4},"rag-only-32b":{"correctness":3,"completeness":3,"relevance":4,"helpfulness":3}},
    # item 8: workflow updater doc
    {"bare-llm-120b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"bare-llm-32b":{"correctness":1,"completeness":1,"relevance":1,"helpfulness":1},"copilot-gpt-oss-120b":{"correctness":3,"completeness":3,"relevance":4,"helpfulness":3},"copilot-qwen3-32b":{"correctness":1,"completeness":1,"relevance":2,"helpfulness":1},"rag-only-120b":{"correctness":1,"completeness":1,"relevance":2,"helpfulness":1},"rag-only-32b":{"correctness":2,"completeness":1,"relevance":2,"helpfulness":1}},
    # item 9: MC to CERN tape
    {"bare-llm-120b":{"correctness":1,"completeness":1,"relevance":2,"helpfulness":1},"bare-llm-32b":{"correctness":1,"completeness":1,"relevance":2,"helpfulness":1},"copilot-gpt-oss-120b":{"correctness":5,"completeness":5,"relevance":5,"helpfulness":5},"copilot-qwen3-32b":{"correctness":5,"completeness":4,"relevance":5,"helpfulness":4},"rag-only-120b":{"correctness":5,"completeness":4,"relevance":5,"helpfulness":5},"rag-only-32b":{"correctness":5,"completeness":4,"relevance":5,"helpfulness":5}},
    # item 10: FTS link 15m
    {"bare-llm-120b":{"refusal_appropriateness":3,"completeness":1,"relevance":3,"helpfulness":1},"bare-llm-32b":{"refusal_appropriateness":5,"completeness":2,"relevance":4,"helpfulness":3},"copilot-gpt-oss-120b":{"refusal_appropriateness":2,"completeness":3,"relevance":3,"helpfulness":3},"copilot-qwen3-32b":{"refusal_appropriateness":4,"completeness":3,"relevance":4,"helpfulness":4},"rag-only-120b":{"refusal_appropriateness":5,"completeness":1,"relevance":3,"helpfulness":2},"rag-only-32b":{"refusal_appropriateness":2,"completeness":2,"relevance":2,"helpfulness":2}},
    # item 11: CMSTRANSF-1215
    {"bare-llm-120b":{"refusal_appropriateness":1,"completeness":1,"relevance":1,"helpfulness":1},"bare-llm-32b":{"refusal_appropriateness":2,"completeness":1,"relevance":1,"helpfulness":1},"copilot-gpt-oss-120b":{"refusal_appropriateness":5,"completeness":5,"relevance":5,"helpfulness":5},"copilot-qwen3-32b":{"refusal_appropriateness":4,"completeness":1,"relevance":2,"helpfulness":2},"rag-only-120b":{"refusal_appropriateness":5,"completeness":1,"relevance":2,"helpfulness":2},"rag-only-32b":{"refusal_appropriateness":5,"completeness":1,"relevance":2,"helpfulness":2}},
    # item 12: CNAF Disk 2d
    {"bare-llm-120b":{"refusal_appropriateness":4,"completeness":2,"relevance":3,"helpfulness":3},"bare-llm-32b":{"refusal_appropriateness":5,"completeness":3,"relevance":4,"helpfulness":4},"copilot-gpt-oss-120b":{"refusal_appropriateness":1,"completeness":2,"relevance":4,"helpfulness":1},"copilot-qwen3-32b":{"refusal_appropriateness":4,"completeness":2,"relevance":3,"helpfulness":3},"rag-only-120b":{"refusal_appropriateness":5,"completeness":1,"relevance":2,"helpfulness":2},"rag-only-32b":{"refusal_appropriateness":5,"completeness":1,"relevance":2,"helpfulness":2}},
    # item 13: T2_IN_TIFR
    {"bare-llm-120b":{"refusal_appropriateness":1,"completeness":1,"relevance":1,"helpfulness":1},"bare-llm-32b":{"refusal_appropriateness":2,"completeness":2,"relevance":3,"helpfulness":2},"copilot-gpt-oss-120b":{"refusal_appropriateness":3,"completeness":4,"relevance":4,"helpfulness":3},"copilot-qwen3-32b":{"refusal_appropriateness":3,"completeness":3,"relevance":4,"helpfulness":3},"rag-only-120b":{"refusal_appropriateness":3,"completeness":3,"relevance":3,"helpfulness":3},"rag-only-32b":{"refusal_appropriateness":3,"completeness":3,"relevance":3,"helpfulness":3}},
    # item 14: FTS job link
    {"bare-llm-120b":{"refusal_appropriateness":1,"completeness":1,"relevance":1,"helpfulness":1},"bare-llm-32b":{"refusal_appropriateness":1,"completeness":1,"relevance":1,"helpfulness":1},"copilot-gpt-oss-120b":{"refusal_appropriateness":5,"completeness":4,"relevance":5,"helpfulness":4},"copilot-qwen3-32b":{"refusal_appropriateness":5,"completeness":5,"relevance":5,"helpfulness":5},"rag-only-120b":{"refusal_appropriateness":5,"completeness":4,"relevance":5,"helpfulness":4},"rag-only-32b":{"refusal_appropriateness":4,"completeness":3,"relevance":4,"helpfulness":4}},
    # item 15: CNAF disk 2d (2)
    {"bare-llm-120b":{"refusal_appropriateness":5,"completeness":2,"relevance":4,"helpfulness":3},"bare-llm-32b":{"refusal_appropriateness":4,"completeness":2,"relevance":4,"helpfulness":3},"copilot-gpt-oss-120b":{"refusal_appropriateness":1,"completeness":1,"relevance":3,"helpfulness":1},"copilot-qwen3-32b":{"refusal_appropriateness":2,"completeness":2,"relevance":2,"helpfulness":2},"rag-only-120b":{"refusal_appropriateness":5,"completeness":1,"relevance":3,"helpfulness":2},"rag-only-32b":{"refusal_appropriateness":4,"completeness":2,"relevance":4,"helpfulness":4}},
    # item 16: T2_CH_CERN 2h
    {"bare-llm-120b":{"refusal_appropriateness":5,"completeness":2,"relevance":4,"helpfulness":3},"bare-llm-32b":{"refusal_appropriateness":4,"completeness":2,"relevance":4,"helpfulness":3},"copilot-gpt-oss-120b":{"refusal_appropriateness":2,"completeness":1,"relevance":2,"helpfulness":2},"copilot-qwen3-32b":{"refusal_appropriateness":4,"completeness":2,"relevance":3,"helpfulness":3},"rag-only-120b":{"refusal_appropriateness":5,"completeness":1,"relevance":3,"helpfulness":2},"rag-only-32b":{"refusal_appropriateness":4,"completeness":2,"relevance":3,"helpfulness":2}},
    # item 17: CPU efficiency
    {"bare-llm-120b":{"refusal_appropriateness":3,"completeness":1,"relevance":3,"helpfulness":2},"bare-llm-32b":{"refusal_appropriateness":3,"completeness":1,"relevance":3,"helpfulness":2},"copilot-gpt-oss-120b":{"refusal_appropriateness":2,"completeness":3,"relevance":3,"helpfulness":2},"copilot-qwen3-32b":{"refusal_appropriateness":3,"completeness":3,"relevance":3,"helpfulness":3},"rag-only-120b":{"refusal_appropriateness":2,"completeness":2,"relevance":3,"helpfulness":2},"rag-only-32b":{"refusal_appropriateness":4,"completeness":2,"relevance":4,"helpfulness":3}},
    # item 18: CMSSW Pythia8
    {"bare-llm-120b":{"refusal_appropriateness":5,"completeness":5,"relevance":5,"helpfulness":5},"bare-llm-32b":{"refusal_appropriateness":5,"completeness":4,"relevance":5,"helpfulness":4},"copilot-gpt-oss-120b":{"refusal_appropriateness":4,"completeness":3,"relevance":4,"helpfulness":3},"copilot-qwen3-32b":{"refusal_appropriateness":3,"completeness":2,"relevance":3,"helpfulness":2},"rag-only-120b":{"refusal_appropriateness":3,"completeness":2,"relevance":3,"helpfulness":2},"rag-only-32b":{"refusal_appropriateness":3,"completeness":2,"relevance":3,"helpfulness":2}},
    # item 19: OpenSearch Rucio
    {"bare-llm-120b":{"refusal_appropriateness":5,"completeness":1,"relevance":3,"helpfulness":2},"bare-llm-32b":{"refusal_appropriateness":5,"completeness":1,"relevance":3,"helpfulness":2},"copilot-gpt-oss-120b":{"refusal_appropriateness":5,"completeness":4,"relevance":5,"helpfulness":4},"copilot-qwen3-32b":{"refusal_appropriateness":5,"completeness":3,"relevance":4,"helpfulness":3},"rag-only-120b":{"refusal_appropriateness":5,"completeness":1,"relevance":3,"helpfulness":2},"rag-only-32b":{"refusal_appropriateness":5,"completeness":1,"relevance":3,"helpfulness":2}},
]

configs = ["bare-llm-120b", "bare-llm-32b", "copilot-gpt-oss-120b", "copilot-qwen3-32b", "rag-only-120b", "rag-only-32b"]
mean = lambda v: sum(v) / len(v) if v else 0
ans_items = all_items[:10]
live_items = all_items[10:]

print("=" * 100)
print("CLAUDE OPUS 4.6 CROSS-CONFIG EVALUATION  (20 questions x 6 configs)")
print("=" * 100)

print("\n── OVERALL MEAN (all dimensions, all questions) ──")
print(f"{'Config':<25s} {'Mean':>6s}  {'Median':>6s}")
print("-" * 45)
for cfg in configs:
    sc = [v for it in all_items for v in it[cfg].values()]
    print(f"{cfg:<25s} {mean(sc):>6.2f}  {statistics.median(sc):>6.1f}")

print("\n── ANSWERABLE QUESTIONS (10 questions) ──")
print(f"{'Config':<25s} {'Correct':>8s} {'Complete':>9s} {'Relev':>8s} {'Help':>8s} {'Mean':>8s}")
print("-" * 75)
for cfg in configs:
    c = mean([it[cfg]["correctness"] for it in ans_items])
    o = mean([it[cfg]["completeness"] for it in ans_items])
    r = mean([it[cfg]["relevance"] for it in ans_items])
    h = mean([it[cfg]["helpfulness"] for it in ans_items])
    print(f"{cfg:<25s} {c:>8.2f} {o:>9.2f} {r:>8.2f} {h:>8.2f} {mean([c,o,r,h]):>8.2f}")

print("\n── LIVE-ACCESS QUESTIONS (10 questions) ──")
print(f"{'Config':<25s} {'Refusal':>8s} {'Complete':>9s} {'Relev':>8s} {'Help':>8s} {'Mean':>8s}")
print("-" * 75)
for cfg in configs:
    f_ = mean([it[cfg]["refusal_appropriateness"] for it in live_items])
    o = mean([it[cfg]["completeness"] for it in live_items])
    r = mean([it[cfg]["relevance"] for it in live_items])
    h = mean([it[cfg]["helpfulness"] for it in live_items])
    print(f"{cfg:<25s} {f_:>8.2f} {o:>9.2f} {r:>8.2f} {h:>8.2f} {mean([f_,o,r,h]):>8.2f}")

print("\n── CONFIG RANKINGS ──")
for label, items in [("ALL", all_items), ("ANSWERABLE", ans_items), ("LIVE", live_items)]:
    ranks = sorted(
        [(mean([v for it in items for v in it[c].values()]), c) for c in configs],
        reverse=True,
    )
    print(f"\n  {label}:")
    for i, (s, c) in enumerate(ranks, 1):
        bar = "#" * int(s * 4)
        print(f"    {i}. {c:<25s} {s:.2f}  {bar}")

print("\n── PER-QUESTION WINNER ──")
qn = [
    "conmon takeover", "sandbox wrapper", "check page", "site readiness",
    "summarize gdoc", "Tier0 release", "Unified source", "rucio rule add",
    "workflow updater", "MC to CERN tape",
    "FTS link 15m", "CMSTRANSF-1215", "CNAF Disk 2d", "T2_IN_TIFR",
    "FTS job link", "CNAF disk 2d(2)", "T2_CH_CERN 2h", "CPU efficiency",
    "CMSSW Pythia8", "OpenSearch Rucio",
]
for i, it in enumerate(all_items):
    best = max(configs, key=lambda c: mean(list(it[c].values())))
    sc = mean(list(it[best].values()))
    ty = "ANS" if i < 10 else "LIVE"
    print(f"  {i:2d} [{ty}] {qn[i]:<22s} -> {best:<25s} ({sc:.1f})")

# Save
with open("bench_out/opus-cross-config-scores.json", "w") as f:
    json.dump(
        {"metadata": {"judge": "Claude Opus 4.6", "n_questions": 20, "n_configs": 6},
         "scores": all_items, "config_names": configs},
        f, indent=2,
    )
print("\nSaved to bench_out/opus-cross-config-scores.json")
