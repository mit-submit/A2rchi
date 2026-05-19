#!/usr/bin/env python3
"""Generate benchmark comparison plots for CHEP 2026 paper / team sharing."""
import json
import math
import os
from collections import Counter, defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import seaborn as sns

# ── Config ───────────────────────────────────────────────────────────────────
FILES = {
    "Agent\n(GPT-5)":        "bench_out/judged/glm-5.1_glm-5.1_run1/gpt5-reference-answers.json",
    "Agent\n(Gemma4-26b)":   "bench_out/judged/glm-5.1_glm-5.1_run1/optimized-tools_compops-gemma4-26b.json",
    "Agent-NoLive\n(Gemma4-26b)": "bench_out/judged/glm-5.1_run1/compops-no-tools_gemma4-26b.json",
    "RAG\n(Gemma4-26b)":     "bench_out/judged/glm-5.1_run1/rag-only_gemma4-26b.json",
    "BareLLM\n(Gemma4-26b)": "bench_out/judged/glm-5.1_run1/bare-llm_gemma4-26b.json",
}

# For legend / titles where newlines are unwanted
LABELS_FLAT = {
    "Agent\n(GPT-5)":              "Agent (GPT-5)",
    "Agent\n(Gemma4-26b)":         "Agent (Gemma4-26b)",
    "Agent-NoLive\n(Gemma4-26b)":  "Agent-NoLive (Gemma4-26b)",
    "RAG\n(Gemma4-26b)":           "RAG (Gemma4-26b)",
    "BareLLM\n(Gemma4-26b)":       "BareLLM (Gemma4-26b)",
}

CATEGORY_LABELS = {
    "data_query":        "Data Query",
    "debugging":         "Debugging",
    "exploratory":       "Exploratory",
    "factual_lookup":    "Factual Lookup",
    "jira_investigation":"Jira Investigation",
    "procedural":        "Procedural",
}

DIMS = ["relevance", "completeness", "specificity", "helpfulness"]
DIM_LABELS = {"relevance": "Relevance", "completeness": "Completeness",
              "specificity": "Specificity", "helpfulness": "Helpfulness"}
DIM_FIELDS = {d: f"llm_judge_{d}" for d in DIMS}

OUTDIR = "bench_out/plots"
os.makedirs(OUTDIR, exist_ok=True)

# ── Palette ──────────────────────────────────────────────────────────────────
COLORS = ["#2196F3", "#4CAF50", "#FF9800", "#9C27B0", "#F44336"]

# ── Data loading ─────────────────────────────────────────────────────────────
with open("configs/submit76/curated_questions_categorized.json") as f:
    cats = json.load(f)
cat_map = {q["question"].strip(): q["category"] for q in cats}
CATEGORIES = sorted(set(cat_map.values()))

# Count questions per category
CAT_COUNTS = Counter(cat_map.values())


def load_scores(path):
    with open(path) as f:
        data = json.load(f)
    sqr = data["benchmarking_results"][0]["single_question_results"]
    scores_by_cat = defaultdict(lambda: {d: [] for d in DIMS})
    for _, q in sqr.items():
        qtxt = q.get("question", "").strip()
        cat = cat_map.get(qtxt, "unknown")
        for d in DIMS:
            v = q.get(DIM_FIELDS[d])
            if v is not None:
                scores_by_cat[cat][d].append(v)
    return scores_by_cat


all_scores = {label: load_scores(p) for label, p in FILES.items()}
config_labels = list(FILES.keys())


def avg(vals):
    return sum(vals) / len(vals) if vals else 0.0


def sem(vals):
    """Standard error of the mean."""
    if len(vals) < 2:
        return 0.0
    m = avg(vals)
    var = sum((v - m) ** 2 for v in vals) / (len(vals) - 1)
    return math.sqrt(var / len(vals))


def cat_label_with_n(cat):
    """Return category label with n= count."""
    return f"{CATEGORY_LABELS[cat]}\n(n={CAT_COUNTS[cat]})"


# ── Helper: annotate bars (above error bar if present) ───────────────────────
def annotate_bars(ax, rects, errs=None, fontsize=8):
    for j, rect in enumerate(rects):
        h = rect.get_height()
        if h > 0:
            offset = (errs[j] if errs is not None else 0) + 0.08
            ax.annotate(f"{h:.2f}",
                        xy=(rect.get_x() + rect.get_width() / 2, h + offset),
                        xytext=(0, 2), textcoords="offset points",
                        ha="center", va="bottom", fontsize=fontsize)


# ── Plot 1: Overall Average by Config (bar chart) ───────────────────────────
def plot_overall_bar():
    fig, ax = plt.subplots(figsize=(8, 5))
    means = []
    errs = []
    for label in config_labels:
        vals = []
        for cat in CATEGORIES:
            for d in DIMS:
                vals.extend(all_scores[label].get(cat, {}).get(d, []))
        means.append(avg(vals))
        errs.append(sem(vals))

    n_total = sum(CAT_COUNTS.values())
    bars = ax.bar(range(len(config_labels)), means, yerr=errs, capsize=4,
                  color=COLORS, edgecolor="white", linewidth=0.8, width=0.6,
                  error_kw=dict(lw=1.2, capthick=1.2))
    annotate_bars(ax, bars, errs=errs, fontsize=10)
    ax.set_xticks(range(len(config_labels)))
    ax.set_xticklabels(config_labels, fontsize=9)
    ax.set_ylabel("Average Score (1–5)", fontsize=11)
    ax.set_title(f"Overall Quality Score by Configuration (n={n_total})",
                 fontsize=13, fontweight="bold")
    ax.set_ylim(0, 5.5)
    ax.yaxis.set_major_locator(ticker.MultipleLocator(1))
    ax.yaxis.set_minor_locator(ticker.MultipleLocator(0.5))
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    fig.tight_layout()
    fig.savefig(f"{OUTDIR}/1_overall_bar.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {OUTDIR}/1_overall_bar.png")


# ── Plot 2: Per-Dimension Overall Scores (grouped bar) ──────────────────────
def plot_dimension_bars():
    n_total = sum(CAT_COUNTS.values())
    fig, ax = plt.subplots(figsize=(10, 5.5))
    x = np.arange(len(DIMS))
    width = 0.15
    offsets = np.arange(len(config_labels)) - (len(config_labels) - 1) / 2

    for i, label in enumerate(config_labels):
        means = []
        errs = []
        for d in DIMS:
            all_v = []
            for cat in CATEGORIES:
                all_v.extend(all_scores[label].get(cat, {}).get(d, []))
            means.append(avg(all_v))
            errs.append(sem(all_v))
        bars = ax.bar(x + offsets[i] * width, means, width * 0.9,
                      yerr=errs, capsize=2, label=LABELS_FLAT[label],
                      color=COLORS[i], edgecolor="white", linewidth=0.5,
                      error_kw=dict(lw=0.8, capthick=0.8))
        annotate_bars(ax, bars, errs=errs, fontsize=7)

    ax.set_xticks(x)
    ax.set_xticklabels([DIM_LABELS[d] for d in DIMS], fontsize=10)
    ax.set_ylabel("Average Score (1–5)", fontsize=11)
    ax.set_title(f"Scores by Evaluation Dimension (n={n_total})",
                 fontsize=13, fontweight="bold")
    ax.set_ylim(0, 5.7)
    ax.yaxis.set_major_locator(ticker.MultipleLocator(1))
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(fontsize=8, loc="upper right", ncol=2)
    fig.tight_layout()
    fig.savefig(f"{OUTDIR}/2_dimension_bars.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {OUTDIR}/2_dimension_bars.png")


# ── Plot 3: Per-Category Overall Average (grouped bar) ──────────────────────
def plot_category_bars():
    fig, ax = plt.subplots(figsize=(13, 5.5))
    x = np.arange(len(CATEGORIES))
    width = 0.15
    offsets = np.arange(len(config_labels)) - (len(config_labels) - 1) / 2

    for i, label in enumerate(config_labels):
        means = []
        errs = []
        for cat in CATEGORIES:
            all_v = []
            for d in DIMS:
                all_v.extend(all_scores[label].get(cat, {}).get(d, []))
            means.append(avg(all_v))
            errs.append(sem(all_v))
        bars = ax.bar(x + offsets[i] * width, means, width * 0.9,
                      yerr=errs, capsize=2, label=LABELS_FLAT[label],
                      color=COLORS[i], edgecolor="white", linewidth=0.5,
                      error_kw=dict(lw=0.8, capthick=0.8))
        annotate_bars(ax, bars, errs=errs, fontsize=6.5)

    ax.set_xticks(x)
    ax.set_xticklabels([cat_label_with_n(c) for c in CATEGORIES], fontsize=9)
    ax.set_ylabel("Average Score (1–5)", fontsize=11)
    ax.set_title("Average Quality Score by Question Category", fontsize=13, fontweight="bold")
    ax.set_ylim(0, 5.7)
    ax.yaxis.set_major_locator(ticker.MultipleLocator(1))
    ax.grid(axis="y", alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(fontsize=8, loc="upper right", ncol=2)
    fig.tight_layout()
    fig.savefig(f"{OUTDIR}/3_category_bars.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {OUTDIR}/3_category_bars.png")


# ── Plot 4: Heatmap (config × category) ─────────────────────────────────────
def plot_heatmap():
    flat_labels = [LABELS_FLAT[l] for l in config_labels]
    cat_labels = [f"{CATEGORY_LABELS[c]} (n={CAT_COUNTS[c]})" for c in CATEGORIES]
    matrix = np.zeros((len(config_labels), len(CATEGORIES)))
    for i, label in enumerate(config_labels):
        for j, cat in enumerate(CATEGORIES):
            vals = []
            for d in DIMS:
                vals.extend(all_scores[label].get(cat, {}).get(d, []))
            matrix[i, j] = avg(vals)

    fig, ax = plt.subplots(figsize=(9, 4.5))
    sns.heatmap(matrix, annot=True, fmt=".2f", cmap="RdYlGn", vmin=1.5, vmax=4.8,
                xticklabels=cat_labels, yticklabels=flat_labels,
                linewidths=0.5, linecolor="white", cbar_kws={"label": "Avg Score"},
                ax=ax)
    ax.set_title("Quality Score Heatmap: Config × Category", fontsize=13, fontweight="bold")
    ax.tick_params(axis="x", rotation=25)
    fig.tight_layout()
    fig.savefig(f"{OUTDIR}/4_heatmap.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {OUTDIR}/4_heatmap.png")


# ── Plot 5: Radar / Spider chart per config ─────────────────────────────────
def plot_radar():
    flat_labels = [LABELS_FLAT[l] for l in config_labels]
    cat_labels = [f"{CATEGORY_LABELS[c]}\n(n={CAT_COUNTS[c]})" for c in CATEGORIES]
    n_cats = len(CATEGORIES)
    angles = np.linspace(0, 2 * np.pi, n_cats, endpoint=False).tolist()
    angles += angles[:1]  # close the polygon

    fig, ax = plt.subplots(figsize=(7, 7), subplot_kw=dict(polar=True))
    for i, label in enumerate(config_labels):
        vals = []
        for cat in CATEGORIES:
            all_v = []
            for d in DIMS:
                all_v.extend(all_scores[label].get(cat, {}).get(d, []))
            vals.append(avg(all_v))
        vals += vals[:1]
        ax.plot(angles, vals, "o-", linewidth=1.8, label=flat_labels[i],
                color=COLORS[i], markersize=4)
        ax.fill(angles, vals, alpha=0.08, color=COLORS[i])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(cat_labels, fontsize=9)
    ax.set_ylim(0, 5)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.set_yticklabels(["1", "2", "3", "4", "5"], fontsize=7, color="grey")
    ax.set_title("Category Performance Radar", fontsize=13, fontweight="bold", pad=20)
    ax.legend(loc="upper right", bbox_to_anchor=(1.35, 1.1), fontsize=8)
    fig.tight_layout()
    fig.savefig(f"{OUTDIR}/5_radar.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {OUTDIR}/5_radar.png")


# ── Plot 6: Dimension breakdown per category (small multiples) ──────────────
def plot_dimension_by_category():
    fig, axes = plt.subplots(2, 3, figsize=(15, 9), sharey=True)
    axes_flat = axes.flatten()
    width = 0.15
    offsets = np.arange(len(config_labels)) - (len(config_labels) - 1) / 2

    for idx, cat in enumerate(CATEGORIES):
        ax = axes_flat[idx]
        x = np.arange(len(DIMS))
        for i, label in enumerate(config_labels):
            means = [avg(all_scores[label].get(cat, {}).get(d, [])) for d in DIMS]
            errs = [sem(all_scores[label].get(cat, {}).get(d, [])) for d in DIMS]
            ax.bar(x + offsets[i] * width, means, width * 0.9,
                   yerr=errs, capsize=1.5,
                   color=COLORS[i], edgecolor="white", linewidth=0.5,
                   error_kw=dict(lw=0.7, capthick=0.7))

        ax.set_xticks(x)
        ax.set_xticklabels(["R", "C", "S", "H"], fontsize=9)
        ax.set_title(f"{CATEGORY_LABELS[cat]} (n={CAT_COUNTS[cat]})",
                     fontsize=11, fontweight="bold")
        ax.set_ylim(0, 5.5)
        ax.yaxis.set_major_locator(ticker.MultipleLocator(1))
        ax.grid(axis="y", alpha=0.3, linestyle="--")
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    # Shared legend
    flat_labels = [LABELS_FLAT[l] for l in config_labels]
    handles = [plt.Rectangle((0, 0), 1, 1, color=COLORS[i]) for i in range(len(config_labels))]
    fig.legend(handles, flat_labels, loc="lower center", ncol=5, fontsize=9,
               bbox_to_anchor=(0.5, -0.02))
    fig.suptitle("Dimension Breakdown by Category (R=Relevance, C=Completeness, S=Specificity, H=Helpfulness)",
                 fontsize=12, fontweight="bold", y=1.01)
    fig.tight_layout()
    fig.savefig(f"{OUTDIR}/6_dimension_by_category.png", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  Saved {OUTDIR}/6_dimension_by_category.png")


# ── Run all ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Generating plots...")
    plot_overall_bar()
    plot_dimension_bars()
    plot_category_bars()
    plot_heatmap()
    plot_radar()
    plot_dimension_by_category()
    print(f"\nAll plots saved to {OUTDIR}/")
