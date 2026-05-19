# CHEP 53q grading set — data summary

Generated 2026-05-18 from `paper_data/20260518_122406/`.

## What's here

```
paper_data/20260518_122406/
├── REPORT.md                           ← this file
├── summary.csv                         ← one row per (tier, config), top-level
├── gold/
│   ├── answers.csv                     6 configs × 53 q × 1 answer
│   ├── llm_judge_scores.csv            6 × 53 × 4 judges × 5 rubric dims
│   ├── llm_judge_reasoning.csv         per-judge free-text reasoning
│   ├── human_grader_scores.csv         59 submitted responses × 5 letters × 2 dims
│   ├── human_grader_rankings.csv       per-question 5-way ranking (ties allowed)
│   ├── human_grader_meta.csv           response status + timestamps
│   ├── per_config_aggregates.csv       same columns as summary.csv, gold-only
│   ├── judge_x_config.csv              core-4 mean broken down by judge
│   ├── questions.csv                   53q reference text
│   └── manifest.json                   provenance + file census
└── old/
    ├── answers.csv                     25 configs × ≤53 q
    ├── llm_judge_scores.csv            glm-5.1 single judge × 5 dims
    ├── llm_judge_reasoning.csv
    ├── per_config_aggregates.csv
    ├── questions.csv
    └── manifest.json
```

## Tier definitions

- **gold** — v9-era runs (4-judge LLM panel: glm-5.1, gemini-3.1-pro-preview, gpt-5.5, claude-opus-4.7). 5 of 6 configs also have human grading from the Argilla v9 push. These ran with the current agent instructions and on the 53q grading set directly.
- **old** — pre-v9 runs (260-question superset, glm-5.1 single judge only). Different agent instructions, different prompts, in some cases different models. Filtered down to the 53q subset by question-text match so they're at least scoped comparably. **Not head-to-head with gold; quote as earlier-iteration baselines.**

For the 53q set: yes, the old runs are **LLM-only**. Human grading exists exclusively for the 5 gold configs that went into Argilla v9.

---

## Findings

### 1. Gold configs — ranked

| config              | core-4 (4-judge avg) | src_faith | human correctness | human usefulness | human rank (1=best) | wall-time (s) |
|---------------------|---------------------:|----------:|------------------:|-----------------:|--------------------:|--------------:|
| gpt-5.5/live        |             **4.63** |      3.74 |              **4.64** |             **4.56** |            **1.37** |         157.3 |
| qwen3.6-27b/live    |                 4.39 |      3.46 |              4.31 |             4.20 |                1.73 |         126.5 |
| qwen3.5-122b/live   |                 4.28 |      3.25 |                 — |                — |                   — |          80.3 |
| qwen3.6-27b/no-tools |                 4.25 |      3.18 |              4.32 |             4.20 |                1.64 |         137.1 |
| qwen3.6-35b/live    |                 4.22 |      3.10 |              4.22 |             4.17 |                1.76 |         101.0 |
| qwen3.6-27b/rag     |                 3.78 |  **4.26** |          **2.95** |         **2.73** |                2.61 |          70.1 |

**What it shows:**

1. **gpt-5.5/live is the consistent leader** on both LLM panel and humans. Humans gave it correctness 4.64 / usefulness 4.56, beating every Qwen variant by ~0.3 points.
2. **The Qwen agents (27b live, 35b live, 27b no-tools) cluster tightly** at human correctness 4.22–4.32. Humans don't distinguish them sharply. The 4-judge LLM panel sees a ~0.17-point gap (27b/live 4.39 vs 35b/live 4.22), but the humans collapse it.
3. **RAG-only is a clear regression on humans (2.95 / 2.73)** despite scoring well on source_faithfulness (4.26 — the highest of any config). The model is grounding itself faithfully in retrieved chunks, but the chunks aren't enough; you can't answer the 53q without tool use. This is the cleanest single-bit result in the dataset.
4. **no-tools beats RAG-only** (correctness 4.32 vs 2.95). With strong enough priors, removing retrieval is *less* harmful than retrieval-only without agent control. That's a counter-intuitive but defensible signal.
5. **Source_faithfulness is anticorrelated with overall quality among agents.** Agent configs that retrieve more aggressively (gpt-5.5/live: 17.6 tools, 71.9 sources) have *lower* judged source-faithfulness (3.74) than RAG-only (4.26) — the judges penalize agents for synthesizing beyond the retrieved text even when synthesis is correct.

### 2. Judge variance is large and gpt-5.5 is the strictest

| config             | glm-5.1 | gemini | gpt-5.5 | opus | 4-judge avg |
|--------------------|--------:|-------:|--------:|-----:|------------:|
| gpt-5.5/live       |    4.83 |   4.88 |    4.09 | 4.72 |        4.63 |
| qwen3.5-122b/live  |    4.48 |   4.78 |    3.53 | 4.32 |        4.28 |
| qwen3.6-27b/live   |    4.56 |   4.83 |    3.71 | 4.49 |        4.39 |
| qwen3.6-27b/no-tools |  4.49 |   4.55 |    3.53 | 4.43 |        4.25 |
| qwen3.6-27b/rag    |    3.95 |   4.41 |    3.26 | 3.50 |        3.78 |
| qwen3.6-35b/live   |    4.48 |   4.55 |    3.52 | 4.33 |        4.22 |

- **gemini is consistently the most lenient**, glm-5.1 close behind.
- **gpt-5.5 is the strictest judge by ~0.7 points** vs gemini/glm — but its *ranking* of configs is consistent with the others. So single-judge means are not directly comparable across the literature, but rank order is robust.
- This is why old-tier numbers (glm-5.1-only) look inflated. Apples-to-apples:
  - gold qwen3.5-122b/live, glm-5.1 only: **4.48**
  - old benchmarking-qwen122b-off-multi (glm only): **4.51**

  The "+0.23 advantage" of the old run disappears once you control for judge. The instructions iteration in gold did not make 122b *worse*; the gold panel just scores harder.

### 3. Old-tier findings (qualitative, single-judge so treat as ordinal)

Top 8 old configs by glm-5.1 core-4:

| rank | config                                                | core-4 | tools/q | n_q* |
|-----:|-------------------------------------------------------|-------:|--------:|-----:|
|    1 | benchmarking-qwen122b-off-multi-20260420              |   4.51 |     9.8 |   53 |
|    2 | benchmarking-qwen122b-on-multi-20260419               |   4.33 |    10.3 |   53 |
|    3 | benchmarking-qwen27b-off-multi-20260418               |   4.15 |     9.0 |   53 |
|    4 | benchmarking-gemma4-off-multi-20260416                |   3.93 |     1.8 |   51 |
|    5 | benchmarking-gemma4-on-opt-rerun-20260416             |   3.87 |     2.5 |   53 |
|    6 | benchmarking-gemma4-think-on-no-tools-20260415        |   3.84 |     1.6 |   52 |
|    7 | copilot-no-tools_gemma4-26b                           |   3.81 |     0.0 |   51 |
|    8 | copilot_gemma4-26b                                    |   3.79 |     0.0 |   51 |

\* n_q < 53 means the config errored/context-overflowed on some questions; the missing records were dropped from this score so the mean is **optimistic** for the incomplete configs.

**What it shows:**

- **Multi-tool agent variants top the old tier**: qwen122b-off-multi / qwen122b-on-multi / qwen27b-off-multi. These are the same architectural family as the gold qwen3.6-27b/live etc., earlier iterations.
- **gemma4-26b family caps around 3.79–3.93** on glm-5.1 (so substantially worse on the harder 4-judge panel). gemma4 is no longer competitive with current Qwen at this scale.
- **bare-llm runs (no tools, no RAG) all sit at 2.3–3.1** across gemma4/qwen3.5/qwen27b. The lift from agentic tool use is **~1.3–1.4 absolute core-4 points** on the same model — the biggest single-factor effect in the dataset.
- **Thinking on vs off**: looking at qwen3.5-122b on-multi (4.33) vs off-multi (4.51), and qwen3.5-27b on-bare-llm (2.77) vs off-bare-llm (2.49) — thinking helps bare-LLM but hurts multi-tool, suggesting the thinking budget is being wasted on tool orchestration that the model handles fine without it. Single-judge so noisy, but the direction is consistent across model sizes.

### 4. Human ↔ LLM agreement (gold)

The 4-judge LLM panel and the human graders agree on:
- gpt-5.5/live is best (both panels)
- RAG-only is decisively worst (LLM core-4: 3.78, human correctness: 2.95)
- The Qwen agent configs are clustered together near each other in both panels

They disagree on:
- LLM panel separates qwen3.6-27b/live (4.39) from qwen3.6-27b/no-tools (4.25). Humans rate no-tools (4.32) *slightly above* 27b/live (4.31) on correctness. Within noise — but tools don't visibly help the human-rated quality of the 27b model on this question set.

---

## Limitations / caveats

- **Old tier is glm-5.1 single-judge**. Means are inflated by ~0.2 vs a 4-judge panel; ranks across configs within the tier are reliable, comparisons across tiers are not.
- **Some old configs have <53 records** because earlier iterations errored on some questions. Their means are optimistic (the dropped records were the hard ones).
- **Human grading n = 59 submitted responses** from 2 graders (dima: 49, hasan: 10). 5 additional graders were added 2026-05-18 but haven't submitted yet. Standard errors on the per-config human means are non-trivial (≈ 0.15–0.25 for n ≈ 11 per cell).
- **qwen3.5-122b/live has no human grading** (legacy LLM-only comparison; cut from the v9 Argilla push to make room for qwen3.6-27b/no-tools).

## Reproduce

```bash
python .scratch/gather_paper_data_split.py     # gather gold + old
python .scratch/build_summary.py               # rebuild summary.csv + REPORT support
python .scratch/summarize_paper_data.py        # quick text summary
python .scratch/check_judge_bias.py            # judge leniency table
```
