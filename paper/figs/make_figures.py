"""Generate paper figures from the raw judged benchmark data.

Reads `bench_out/judged/glm-5.1_run2/*.json` and the categorised question
set at `configs/submit76/curated_questions_categorized.json`, and writes
PDF figures to `paper/figs/`.

Figures produced:
  fig_workload.pdf       --- workload composition (categories x answerability)
  fig_headline.pdf       --- overall scores: 4 pipelines x 3 models + GPT-5.3 ref
  fig_by_category.pdf    --- pipeline x category mean scores (heatmap)
  fig_quality_latency.pdf --- mean score vs median wall time per config

All figures use a colour-blind-safe palette and are sized for either
single-column (~3.4in) or full-width (~6.8in) inclusion at the venue's
two-column page width. Saved as PDF for vector rendering.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

# Match the LaTeX body text (Computer Modern) so figure labels read as part
# of the paper, not as imported sans-serif. Requires a working pdflatex on
# the system PATH (already a build prerequisite for the paper).
matplotlib.rcParams.update({
    "text.usetex": True,
    "font.family": "serif",
    "font.serif": ["Computer Modern Roman"],
    "font.size": 9,
    "axes.labelsize": 9,
    "axes.titlesize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "figure.dpi": 150,
})

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
JUDGED = ROOT / "bench_out" / "judged" / "glm-5.1_run2"
DATASET = ROOT / "configs" / "submit76" / "curated_questions_categorized.json"
OUT = ROOT / "paper" / "figs"
OUT.mkdir(exist_ok=True)

# -----------------------------------------------------------------------------
# Style
# -----------------------------------------------------------------------------

# Wong palette (colour-blind safe), 8 hues, high contrast against white.
PALETTE = {
    "black":    "#000000",
    "orange":   "#E69F00",
    "skyblue":  "#56B4E9",
    "green":    "#009E73",
    "yellow":   "#F0E442",
    "blue":     "#0072B2",
    "red":      "#D55E00",
    "magenta":  "#CC79A7",
}

PIPELINE_COLOR = {
    "Bare LLM":         PALETTE["red"],
    "RAG":              PALETTE["orange"],
    "Agent (no live tools)": PALETTE["skyblue"],
    "Agent":            PALETTE["blue"],
}

PIPELINE_ORDER = ["Bare LLM", "RAG", "Agent (no live tools)", "Agent"]
MODEL_ORDER = ["gemma4-26b", "qwen3.5-27b", "qwen3.5-122b"]
MODEL_DISPLAY = {
    "gemma4:26b":         "gemma4-26b",
    "qwen3.5:27b":        "qwen3.5-27b",
    "qwen3.5:122b-a10b":  "qwen3.5-122b",
}
CATEGORY_ORDER = [
    "factual_lookup", "procedural", "exploratory",
    "data_query", "jira_investigation", "debugging",
]
CATEGORY_DISPLAY = {
    "factual_lookup":     "Factual lookup",
    "procedural":         "Procedural",
    "exploratory":        "Exploratory",
    "data_query":         "Data query",
    "jira_investigation": "Jira investigation",
    "debugging":          "Debugging",
}

plt.rcParams.update({
    "font.family":      "serif",
    "font.size":        11,
    "axes.titlesize":   12,
    "axes.labelsize":   11,
    "xtick.labelsize":  10,
    "ytick.labelsize":  10,
    "legend.fontsize":  10,
    "axes.spines.top":   False,
    "axes.spines.right": False,
    "axes.linewidth":    0.8,
    "axes.grid":         False,
    "pdf.fonttype":      42,   # embed TrueType, not Type-3 bitmap
    "ps.fonttype":       42,
    "savefig.bbox":      "tight",
    "savefig.pad_inches": 0.02,
})

DIMS = ["llm_judge_relevance", "llm_judge_completeness",
        "llm_judge_specificity", "llm_judge_helpfulness"]
DIMS_ALL = DIMS + ["llm_judge_source_faithfulness"]
DIM_DISPLAY = {
    "llm_judge_relevance":         "Relevance",
    "llm_judge_completeness":      "Completeness",
    "llm_judge_specificity":       "Specificity",
    "llm_judge_helpfulness":       "Helpfulness",
    "llm_judge_source_faithfulness": "Source faithfulness",
}

# Tool families: prefix-match against the tool_name in trace_events.
TOOL_FAMILIES = [
    ("Documentation",  ("search_vectorstore", "search_local_files",
                        "search_metadata_index", "list_metadata_schema",
                        "fetch_catalog_document")),
    ("Rucio MonIT",    ("rucio_events", "monit_opensearch", "search_rucio_events",
                        "monit_fetch_rucio", "fetch_rucio_document")),
    ("HTCondor MonIT", ("condor_metric", "condor_opensearch", "search_condor_metric",
                        "monit_fetch_condor", "fetch_condor_document")),
]
TOOL_FAMILY_COLOR = {
    "Documentation":  PALETTE["green"],
    "Rucio MonIT":    PALETTE["blue"],
    "HTCondor MonIT": PALETTE["orange"],
}


def classify_tool(tool_name: str) -> str | None:
    if not tool_name:
        return None
    name = tool_name.lower()
    for family, prefixes in TOOL_FAMILIES:
        if any(name.startswith(p) or p in name for p in prefixes):
            return family
    return None

# -----------------------------------------------------------------------------
# Config-name parsing
# -----------------------------------------------------------------------------

def parse_config(cfg_path: str, file_name: str) -> tuple[str, str, str] | None:
    """Map a (configuration_file, file_name) pair to (pipeline, model, thinking).

    Returns None for configs we exclude (okg-* knowledge-graph variants).
    """
    cfg = cfg_path.lower()

    # Skip knowledge-graph experimental variants.
    if "okg" in file_name.lower():
        return None

    # Pipeline.
    if "no-tools" in cfg:
        pipeline = "Agent (no live tools)"
    elif "optimized-tools" in cfg:
        pipeline = "Agent"
    elif "rag-only" in cfg:
        pipeline = "RAG"
    elif "bare-llm" in file_name.lower():
        pipeline = "Bare LLM"
    elif cfg.endswith("config.yaml") and "opt-rerun" in file_name.lower():
        # gemma4-on-opt-rerun is the agent (re-run for thinking-on)
        pipeline = "Agent"
    else:
        return None

    # Model.
    if "gemma4" in cfg or "gemma4" in file_name.lower():
        model = "gemma4-26b"
    elif "122b" in cfg or "qwen122b" in file_name.lower():
        model = "qwen3.5-122b"
    elif "27b" in cfg or "qwen27b" in file_name.lower():
        model = "qwen3.5-27b"
    else:
        return None

    # Thinking on/off.
    fn = file_name.lower()
    if "thinking-on" in cfg or re.search(r"-on-(bare-llm|multi|opt)", fn):
        thinking = "on"
    elif "thinking-off" in cfg or re.search(r"-off-(bare-llm|multi)", fn):
        thinking = "off"
    else:
        thinking = "off"  # default

    return pipeline, model, thinking


# -----------------------------------------------------------------------------
# Data loading
# -----------------------------------------------------------------------------

def load_question_categories() -> dict[str, str]:
    """Map normalised question text -> category label."""
    with DATASET.open() as f:
        qs = json.load(f)
    return {q["question"].strip(): q["category"] for q in qs}


def overall_score(row: dict) -> float | None:
    """Mean of the four core dimensions, or None if any are missing."""
    vals = [row.get(d) for d in DIMS]
    if any(v is None for v in vals):
        return None
    return float(mean(vals))


def load_results():
    """Load every (pipeline, model, thinking) -> list-of-rows."""
    cats = load_question_categories()
    data: dict[tuple[str, str, str], list[dict]] = defaultdict(list)

    for fp in sorted(JUDGED.glob("*.json")):
        with fp.open() as f:
            judged = json.load(f)
        for entry in judged.get("benchmarking_results", []):
            key = parse_config(entry.get("configuration_file", ""), fp.name)
            if key is None:
                continue
            sqr = entry.get("single_question_results") or {}
            if not isinstance(sqr, dict):
                continue
            for qid, row in sqr.items():
                if "llm_judge_relevance" not in row:
                    continue
                row = dict(row)
                row["_question"] = (row.get("question") or "").strip()
                row["_category"] = cats.get(row["_question"])
                row["_overall"]  = overall_score(row)
                data[key].append(row)
    return data


def load_gpt5_reference():
    """The production GPT-5 answers were judged separately; load as a single config."""
    # Look in the run1 directory for the gpt5-reference-answers.json file.
    candidates = [
        ROOT / "bench_out" / "judged" / "glm-5.1_glm-5.1_run1" / "gpt5-reference-answers.json",
        ROOT / "bench_out" / "judged" / "glm-5.1_run1" / "gpt5-reference-answers.json",
    ]
    fp = next((c for c in candidates if c.exists()), None)
    if fp is None:
        return None
    cats = load_question_categories()
    with fp.open() as f:
        judged = json.load(f)
    rows = []
    for entry in judged.get("benchmarking_results", []):
        sqr = entry.get("single_question_results") or {}
        if not isinstance(sqr, dict):
            continue
        for qid, row in sqr.items():
            if "llm_judge_relevance" not in row:
                continue
            r = dict(row)
            r["_question"] = (r.get("question") or "").strip()
            r["_category"] = cats.get(r["_question"])
            r["_overall"]  = overall_score(r)
            rows.append(r)
    return rows


# -----------------------------------------------------------------------------
# Aggregation helpers
# -----------------------------------------------------------------------------

def mean_overall(rows: list[dict]) -> tuple[float, float, int]:
    """Mean and standard error of the per-row overall score."""
    vals = [r["_overall"] for r in rows if r["_overall"] is not None]
    if not vals:
        return float("nan"), float("nan"), 0
    m = float(np.mean(vals))
    se = float(np.std(vals, ddof=1) / np.sqrt(len(vals))) if len(vals) > 1 else 0.0
    return m, se, len(vals)


def mean_by_category(rows: list[dict]) -> dict[str, float]:
    """Mean overall score per category."""
    by = defaultdict(list)
    for r in rows:
        if r["_overall"] is None or r["_category"] is None:
            continue
        by[r["_category"]].append(r["_overall"])
    return {c: float(np.mean(vs)) for c, vs in by.items() if vs}


def median_wall_time(rows: list[dict]) -> float:
    """Median wall time per question in seconds."""
    vals = [r.get("time_elapsed") for r in rows if r.get("time_elapsed") is not None]
    return float(median(vals)) if vals else float("nan")


# -----------------------------------------------------------------------------
# Figure 1: workload composition
# -----------------------------------------------------------------------------

def fig_workload():
    """Each question is exclusively doc-answerable OR live-access by category,
    so the figure shows one bar per category coloured by group, with a divider
    between the two groups rather than stacking.
    """
    with DATASET.open() as f:
        qs = json.load(f)
    counts = defaultdict(lambda: [0, 0])  # category -> [doc, live]
    for q in qs:
        cat = q["category"]
        if q.get("answerable_from_docs"):
            counts[cat][0] += 1
        else:
            counts[cat][1] += 1

    cats_in_order = [c for c in CATEGORY_ORDER if c in counts]
    labels = [CATEGORY_DISPLAY[c] for c in cats_in_order]
    counts_arr = [counts[c][0] + counts[c][1] for c in cats_in_order]
    is_doc = [counts[c][0] > 0 for c in cats_in_order]
    colors = [PALETTE["green"] if d else PALETTE["red"] for d in is_doc]

    fig, ax = plt.subplots(figsize=(3.4, 2.5))
    y = np.arange(len(labels))
    ax.barh(y, counts_arr, color=colors, edgecolor="black", linewidth=0.4)
    for i, n in enumerate(counts_arr):
        ax.text(n + 1.5, i, str(n), va="center", fontsize=9)

    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Number of questions")
    ax.set_xlim(0, max(counts_arr) + 16)

    # Divider between the doc-answerable group (top) and live-access group.
    n_doc = sum(is_doc)
    ax.axhline(n_doc - 0.5, color="black", linewidth=0.6)

    # Right-side group annotations.
    xmax = ax.get_xlim()[1]
    ax.text(xmax * 0.99, (n_doc - 1) / 2, "answerable\nfrom docs",
            ha="right", va="center", fontsize=8.5, color=PALETTE["green"],
            fontweight="bold")
    ax.text(xmax * 0.99, n_doc + (len(labels) - n_doc - 1) / 2,
            "requires\nlive tool", ha="right", va="center",
            fontsize=8.5, color=PALETTE["red"], fontweight="bold")

    fig.tight_layout()
    fig.savefig(OUT / "fig_workload.pdf", bbox_inches="tight")
    plt.close(fig)


# -----------------------------------------------------------------------------
# Figure 2: headline overall scores
# -----------------------------------------------------------------------------

def fig_headline(data, ref_rows):
    """Grouped bar: pipelines on x-axis, models as bar groups within.

    Picks the higher-scoring thinking variant per (pipeline, model) cell.
    """
    # Build matrix [pipeline][model] -> (mean, se, n)
    cell = {}
    for (pipeline, model, thinking), rows in data.items():
        m, se, n = mean_overall(rows)
        prev = cell.get((pipeline, model))
        if prev is None or m > prev[0]:
            cell[(pipeline, model)] = (m, se, n)

    n_pipelines = len(PIPELINE_ORDER)
    n_models = len(MODEL_ORDER)
    width = 0.8 / n_models
    x = np.arange(n_pipelines)

    fig, ax = plt.subplots(figsize=(6.8, 3.6))
    model_palette = [PALETTE["skyblue"], PALETTE["orange"], PALETTE["blue"]]
    for j, model in enumerate(MODEL_ORDER):
        means = [cell.get((p, model), (np.nan,))[0] for p in PIPELINE_ORDER]
        errs  = [cell.get((p, model), (np.nan, np.nan))[1] for p in PIPELINE_ORDER]
        offset = (j - (n_models - 1) / 2) * width
        bars = ax.bar(x + offset, means, width=width * 0.92,
                      yerr=errs, capsize=2.5,
                      color=model_palette[j], edgecolor="black", linewidth=0.5,
                      label=model, error_kw=dict(lw=0.7))
        # Numeric annotations above each bar.
        for xpos, m in zip(x + offset, means):
            if not np.isnan(m):
                ax.text(xpos, m + 0.04, f"{m:.2f}",
                        ha="center", va="bottom", fontsize=8)

    # GPT-5.3 reference horizontal line.
    if ref_rows:
        ref_m, _, _ = mean_overall(ref_rows)
        ax.axhline(ref_m, color=PALETTE["black"], linestyle="--", linewidth=1.2,
                   label=f"GPT-5.3 reference ({ref_m:.2f})")

    ax.set_xticks(x)
    ax.set_xticklabels(PIPELINE_ORDER)
    ax.set_ylabel("Mean rubric score (1--5)")
    ax.set_ylim(2.0, 4.9)
    ax.yaxis.set_major_locator(plt.MultipleLocator(0.5))
    ax.yaxis.grid(True, linestyle=":", alpha=0.5)
    ax.set_axisbelow(True)

    # Legend goes above the plot; the model bars sit in the upper half so an
    # in-axes legend would overlap them at any of the four corners.
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02),
              frameon=False, ncols=4,
              handletextpad=0.4, columnspacing=1.0)
    fig.tight_layout()
    fig.savefig(OUT / "fig_headline.pdf", bbox_inches="tight")
    plt.close(fig)


# -----------------------------------------------------------------------------
# Figure 3: per-category heatmap
# -----------------------------------------------------------------------------

def fig_by_category(data, ref_rows):
    """Heatmap: rows = (pipeline, model) cells + GPT-5.3 reference, cols = categories."""
    # Pick best thinking variant per cell.
    cell_rows = {}
    for (pipeline, model, thinking), rows in data.items():
        prev_n = len(cell_rows.get((pipeline, model), []))
        if not cell_rows.get((pipeline, model)) or len(rows) > prev_n:
            cell_rows[(pipeline, model)] = rows

    row_labels = []
    matrix = []
    for pipeline in PIPELINE_ORDER:
        for model in MODEL_ORDER:
            rows = cell_rows.get((pipeline, model), [])
            by_cat = mean_by_category(rows)
            matrix.append([by_cat.get(c, np.nan) for c in CATEGORY_ORDER])
            row_labels.append(f"{pipeline} -- {model}")

    if ref_rows:
        by_cat = mean_by_category(ref_rows)
        matrix.append([by_cat.get(c, np.nan) for c in CATEGORY_ORDER])
        row_labels.append("GPT-5.3 reference")

    M = np.array(matrix)

    fig, ax = plt.subplots(figsize=(6.8, 5.4))
    im = ax.imshow(M, aspect="auto", cmap="RdYlGn", vmin=3.0, vmax=4.7)

    ax.set_xticks(np.arange(len(CATEGORY_ORDER)))
    ax.set_xticklabels([CATEGORY_DISPLAY[c] for c in CATEGORY_ORDER],
                       rotation=30, ha="right")
    ax.set_yticks(np.arange(len(row_labels)))
    ax.set_yticklabels(row_labels)

    # Cell text.
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            v = M[i, j]
            if np.isnan(v):
                continue
            color = "black" if v > 3.0 else "white"
            ax.text(j, i, f"{v:.2f}", ha="center", va="center",
                    color=color, fontsize=8)

    # Visually separate the production reference from the configurations.
    if ref_rows:
        ax.axhline(len(row_labels) - 1.5, color="black", linewidth=1.2)

    cbar = fig.colorbar(im, ax=ax, fraction=0.035, pad=0.02)
    cbar.set_label("Mean rubric score (1--5)")
    cbar.outline.set_visible(False)
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_visible(False)
    ax.tick_params(length=0)
    fig.tight_layout()
    fig.savefig(OUT / "fig_by_category.pdf", bbox_inches="tight")
    plt.close(fig)


# -----------------------------------------------------------------------------
# Figure 4: quality vs latency
# -----------------------------------------------------------------------------

def fig_quality_latency(data, ref_rows):
    """Mean score against median wall time, log-time x-axis.

    Marker shape encodes pipeline; marker fill encodes thinking on/off.
    The reader can infer model from horizontal position --- larger models
    sit at higher wall times within each pipeline cluster --- so we do not
    annotate every point.
    """
    fig, ax = plt.subplots(figsize=(3.4, 2.8))

    marker_for = {
        "Bare LLM":         "o",
        "RAG":              "s",
        "Agent (no live tools)": "^",
        "Agent":            "D",
    }

    for (pipeline, model, thinking), rows in data.items():
        m, _, _ = mean_overall(rows)
        t = median_wall_time(rows)
        if np.isnan(m) or np.isnan(t):
            continue
        face = PIPELINE_COLOR[pipeline] if thinking == "off" else "white"
        edge = PIPELINE_COLOR[pipeline]
        ax.scatter([t], [m],
                   marker=marker_for[pipeline],
                   s=55,
                   facecolor=face, edgecolor=edge, linewidth=1.3,
                   zorder=3)

    # GPT-5.3 reference horizontal line.
    if ref_rows:
        ref_m, _, _ = mean_overall(ref_rows)
        ax.axhline(ref_m, color=PALETTE["black"], linestyle="--",
                   linewidth=1, zorder=2)
        ax.text(5.8, ref_m + 0.05, f"GPT-5.3 ref ({ref_m:.2f})",
                color=PALETTE["black"])

    ax.set_xscale("log")
    ax.set_xlabel("Median wall time per question (s)")
    ax.set_ylabel("Mean rubric score (1--5)")
    ax.set_ylim(2.0, 4.7)
    ax.set_xlim(5, 250)
    ax.yaxis.grid(True, linestyle=":", alpha=0.5)
    ax.set_axisbelow(True)

    # Two-column legend INSIDE the plot in the lower-right quadrant where
    # the data has no points (RAG and Agent points sit upper-half between
    # x=20 and x=200; Bare LLM sits left at low score). Two columns keep
    # each label compact and avoid clipping at column-width.
    pipeline_handles = [
        plt.Line2D([0], [0], marker=marker_for[p], linestyle="",
                   markerfacecolor=PIPELINE_COLOR[p],
                   markeredgecolor=PIPELINE_COLOR[p],
                   markersize=6.5, label=p)
        for p in PIPELINE_ORDER
    ]
    ax.legend(handles=pipeline_handles,
              loc="lower right", ncols=2,
              frameon=True, framealpha=0.9, edgecolor="0.7",
              handletextpad=0.4, columnspacing=0.9,
              borderaxespad=0.4)

    fig.tight_layout()
    fig.savefig(OUT / "fig_quality_latency.pdf", bbox_inches="tight")
    plt.close(fig)


# -----------------------------------------------------------------------------
# Figure 5: per-dimension breakdown
# -----------------------------------------------------------------------------

def fig_per_dimension(data, ref_rows):
    """For the largest model, plot every rubric dimension per pipeline.

    The four core dimensions plus source faithfulness.
    Bars are grouped by dimension; pipelines are colour-coded.
    """
    target_model = "qwen3.5-122b"

    # Pick best thinking variant per pipeline at the target model.
    pipeline_rows = {}
    for (pipeline, model, thinking), rows in data.items():
        if model != target_model:
            continue
        m, _, _ = mean_overall(rows)
        prev = pipeline_rows.get(pipeline)
        if prev is None or m > prev[1]:
            pipeline_rows[pipeline] = (rows, m)

    # Compute per-dimension means.
    def dim_mean(rows, dim):
        vals = [r.get(dim) for r in rows
                if r.get(dim) is not None]
        return float(np.mean(vals)) if vals else float("nan")

    # Reference (single configuration, doesn't have a thinking variant).
    ref_means = {d: dim_mean(ref_rows, d) for d in DIMS_ALL} if ref_rows else None

    n_dim = len(DIMS_ALL)
    width = 0.18
    x = np.arange(n_dim)

    # Sized for two-column inclusion at \textwidth (~6.8 inches);
    # 5 dimension labels need the horizontal room.
    fig, ax = plt.subplots(figsize=(6.8, 2.8))

    pipelines = ["Bare LLM", "RAG", "Agent (no live tools)", "Agent"]
    n_pipe = len(pipelines)
    for j, pipeline in enumerate(pipelines):
        rows, _ = pipeline_rows.get(pipeline, ([], None))
        means = [dim_mean(rows, d) for d in DIMS_ALL]
        offset = (j - (n_pipe - 1) / 2) * width
        ax.bar(x + offset, means, width=width * 0.92,
               color=PIPELINE_COLOR[pipeline], edgecolor="black",
               linewidth=0.4, label=pipeline)

    if ref_means:
        # GPT-5.3 reference as small black markers above each dim group.
        for i, d in enumerate(DIMS_ALL):
            v = ref_means.get(d)
            if v is None or np.isnan(v):
                continue
            ax.scatter([x[i]], [v], marker="_", s=180,
                       color=PALETTE["black"], linewidth=2.0, zorder=5)
        ax.scatter([], [], marker="_", s=180, color=PALETTE["black"],
                   linewidth=2.0, label="GPT-5.3 ref")

    ax.set_xticks(x)
    ax.set_xticklabels([DIM_DISPLAY[d] for d in DIMS_ALL])
    ax.set_ylabel("Mean rubric score (1--5)")
    ax.set_ylim(2.0, 5.0)
    ax.yaxis.set_major_locator(plt.MultipleLocator(0.5))
    ax.yaxis.grid(True, linestyle=":", alpha=0.5)
    ax.set_axisbelow(True)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02),
              frameon=False, ncols=5,
              handletextpad=0.5, columnspacing=1.5)
    fig.tight_layout()
    fig.savefig(OUT / "fig_per_dimension.pdf", bbox_inches="tight")
    plt.close(fig)


# -----------------------------------------------------------------------------
# Figure 6: tool usage by category
# -----------------------------------------------------------------------------

def fig_tool_usage(data):
    """Mean tool calls per question, by question category and tool family,
    for the agent (with full tools) on the largest model. Stacked bar."""
    target = ("Agent", "qwen3.5-122b", "on")
    rows = data.get(target)
    if not rows:
        # Fall back to thinking-off variant.
        rows = data.get(("Agent", "qwen3.5-122b", "off"))
    if not rows:
        return

    # By category and family: count tool calls.
    by_cat_family = defaultdict(lambda: defaultdict(int))
    counts_per_cat = Counter()
    for r in rows:
        cat = r.get("_category")
        if not cat:
            continue
        counts_per_cat[cat] += 1
        for ev in r.get("trace_events", []):
            if not isinstance(ev, dict) or ev.get("type") != "tool_start":
                continue
            family = classify_tool(ev.get("tool_name"))
            if family is None:
                continue
            by_cat_family[cat][family] += 1

    cats_in_order = [c for c in CATEGORY_ORDER if counts_per_cat.get(c, 0) > 0]
    labels = [CATEGORY_DISPLAY[c] for c in cats_in_order]

    families = ["Documentation", "Rucio MonIT", "HTCondor MonIT"]
    # Mean calls per question of each family per category.
    mean_calls = {f: [] for f in families}
    for c in cats_in_order:
        n_q = counts_per_cat[c]
        for f in families:
            mean_calls[f].append(by_cat_family[c].get(f, 0) / n_q)

    # Sized for single-column inclusion at \columnwidth (~3.4 inches).
    fig, ax = plt.subplots(figsize=(3.4, 2.6))
    y = np.arange(len(labels))
    height = 0.65

    left = np.zeros(len(labels))
    for f in families:
        vals = np.array(mean_calls[f])
        ax.barh(y, vals, height=height, left=left,
                color=TOOL_FAMILY_COLOR[f], edgecolor="black", linewidth=0.4,
                label=f)
        left += vals

    # Total annotation at end of each bar.
    for i, total in enumerate(left):
        ax.text(total + 0.1, i, f"{total:.1f}", va="center", fontsize=9)

    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    ax.set_xlabel("Mean tool calls per question")
    ax.set_xlim(0, max(left) * 1.12 + 1)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02),
              frameon=False, ncols=3,
              handletextpad=0.4, columnspacing=0.8)
    fig.tight_layout()
    fig.savefig(OUT / "fig_tool_usage.pdf", bbox_inches="tight")
    plt.close(fig)


# -----------------------------------------------------------------------------
# Figure 7: system architecture
# -----------------------------------------------------------------------------

def fig_architecture():
    """Three-column block diagram of the deployed Archi agent.

    Left:  two source groups (offline-ingested documentation, live MonIT).
    Mid:   the agent, with the ReAct loop drawn inside the box.
    Right: operator interacting through the chat interface.
    """
    from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

    # Coordinate system: 130 wide x 56 tall. figsize matches the ratio so the
    # diagram fills the figure without dead space.
    W, H = 130, 56
    fig_w = 6.8
    fig_h = fig_w * H / W
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.set_aspect("equal")
    ax.axis("off")

    DOC_FILL,   DOC_EDGE   = "#E6F2EA", PALETTE["green"]
    LIVE_FILL,  LIVE_EDGE  = "#FBE6D8", PALETTE["red"]
    AGENT_FILL, AGENT_EDGE = "#DCEAF8", PALETTE["blue"]
    CHAT_FILL              = "#F4F4F4"

    def box(x, y, w, h, fc, ec=PALETTE["black"], lw=1.0):
        b = FancyBboxPatch((x, y), w, h,
                           boxstyle="round,pad=0.4,rounding_size=1.0",
                           linewidth=lw, edgecolor=ec, facecolor=fc)
        ax.add_patch(b)

    def arrow(x1, y1, x2, y2, dashed=False, lw=0.9):
        a = FancyArrowPatch((x1, y1), (x2, y2),
                            arrowstyle="-|>", mutation_scale=11,
                            linewidth=lw, color=PALETTE["black"],
                            linestyle="--" if dashed else "-")
        ax.add_patch(a)

    # ---- Left column: source groups -----------------------------------------
    # Documentation sources
    box(2, 34, 34, 18, fc=DOC_FILL, ec=DOC_EDGE, lw=1.2)
    ax.text(19, 49.5, "Documentation", fontsize=10, fontweight="bold",
            color=DOC_EDGE, ha="center")
    ax.text(19, 45,   "CMS TWiki",                 fontsize=8.5, ha="center")
    ax.text(19, 41.5, "CompOps Jira tickets",      fontsize=8.5, ha="center")
    ax.text(19, 38,   "public CMS doc repositories", fontsize=8.5, ha="center")

    # Live MonIT sources
    box(2, 4, 34, 18, fc=LIVE_FILL, ec=LIVE_EDGE, lw=1.2)
    ax.text(19, 19.5, "Live MonIT", fontsize=10, fontweight="bold",
            color=LIVE_EDGE, ha="center")
    ax.text(19, 14.5, "Rucio events",        fontsize=8.5, ha="center")
    ax.text(19, 10,   "HTCondor job metrics", fontsize=8.5, ha="center")

    # ---- Middle column: agent box -------------------------------------------
    AX1, AX2 = 50, 88
    AY1, AY2 = 8, 48
    box(AX1, AY1, AX2 - AX1, AY2 - AY1, fc=AGENT_FILL, ec=AGENT_EDGE, lw=1.2)
    ax.text(69, 43, "Agent", fontsize=11, fontweight="bold",
            color=AGENT_EDGE, ha="center")
    ax.text(69, 39, "(ReAct state graph)", fontsize=8,
            color=AGENT_EDGE, ha="center", style="italic")

    # Inner schematic: reason <-> tool call, then -> compose answer.
    ax.text(60, 28, "reason",    fontsize=9, ha="center")
    ax.text(78, 28, "tool call", fontsize=9, ha="center")
    ax.annotate("", xy=(73.5, 28), xytext=(64.5, 28),
                arrowprops=dict(arrowstyle="<|-|>",
                                color=PALETTE["black"], lw=0.7))
    ax.annotate("", xy=(69, 17), xytext=(69, 25),
                arrowprops=dict(arrowstyle="-|>",
                                color=PALETTE["black"], lw=0.7))
    ax.text(69, 13.5, "compose answer", fontsize=9, ha="center")

    # ---- Right column: chat + operator --------------------------------------
    box(102, 20, 18, 16, fc=CHAT_FILL, lw=1.0)
    ax.text(111, 28, "Chat",      fontsize=9.5, ha="center")
    ax.text(111, 24, "interface", fontsize=9.5, ha="center")
    ax.text(111, 7,  "Operator",  fontsize=9.5, ha="center", style="italic")

    # ---- Arrows: source -> agent --------------------------------------------
    # Documentation -> agent. Dashed = the documentation is offline-ingested
    # into the agent's retrieval index, then queried per-question.
    arrow(36, 43, AX1, 38, dashed=True)
    ax.text(46, 42.5, "retrieve", fontsize=8, ha="center", style="italic",
            color=PALETTE["green"])

    # Live MonIT -> agent. Solid = per-question live query.
    arrow(36, 13, AX1, 18)
    ax.text(46, 17.5, "live query", fontsize=8, ha="center", style="italic",
            color=PALETTE["red"])

    # ---- Arrows: agent <-> chat <-> operator --------------------------------
    arrow(AX2, 30, 102, 30)
    ax.text(95, 32, "answer", fontsize=8, ha="center", style="italic")
    arrow(102, 26, AX2, 26)
    ax.text(95, 23.5, "question", fontsize=8, ha="center", style="italic")

    # Operator <-> chat (vertical pair on the right).
    arrow(108, 13, 108, 20)
    arrow(114, 20, 114, 13)

    fig.savefig(OUT / "fig_architecture.pdf")
    plt.close(fig)


# -----------------------------------------------------------------------------
# Console summary table for the paper text
# -----------------------------------------------------------------------------

def print_summary(data, ref_rows):
    print(f"{'pipeline':18s} {'model':14s} {'think':5s} {'mean':>5s} {'se':>5s}  N    median_wall(s)")
    rows_summary = []
    for (pipeline, model, thinking), rows in sorted(data.items()):
        m, se, n = mean_overall(rows)
        t = median_wall_time(rows)
        print(f"{pipeline:18s} {model:14s} {thinking:5s} {m:5.2f} {se:5.2f} {n:4d}   {t:6.1f}")
        rows_summary.append((pipeline, model, thinking, m, se, n, t))
    if ref_rows:
        m, se, n = mean_overall(ref_rows)
        print(f"{'GPT-5.3 reference':18s} {'---':14s} {'---':5s} {m:5.2f} {se:5.2f} {n:4d}   {'---':>6s}")
    return rows_summary


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    print(f"Reading judged data from {JUDGED}")
    data = load_results()
    ref_rows = load_gpt5_reference()
    if ref_rows:
        print(f"GPT-5.3 reference: {len(ref_rows)} rows loaded")
    print()
    print_summary(data, ref_rows)
    print()
    # Architecture diagram is a TikZ figure rendered inline in 03_agent.tex;
    # it is not generated here.
    fig_workload()
    print(f"  wrote {OUT / 'fig_workload.pdf'}")
    fig_headline(data, ref_rows)
    print(f"  wrote {OUT / 'fig_headline.pdf'}")
    fig_by_category(data, ref_rows)
    print(f"  wrote {OUT / 'fig_by_category.pdf'}")
    fig_quality_latency(data, ref_rows)
    print(f"  wrote {OUT / 'fig_quality_latency.pdf'}")
    fig_per_dimension(data, ref_rows)
    print(f"  wrote {OUT / 'fig_per_dimension.pdf'}")
    fig_tool_usage(data)
    print(f"  wrote {OUT / 'fig_tool_usage.pdf'}")


if __name__ == "__main__":
    main()
