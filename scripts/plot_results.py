#!/usr/bin/env python3
"""Generate evaluation result plots for CHEP 2026 paper.

Dynamically discovers configs from bench_out/judged/<judge_run>/ directories
or falls back to bench_out/judge_results_v4/ (legacy batch format).

Usage:
    python scripts/plot_results.py                           # auto-detect latest judged run
    python scripts/plot_results.py --results-dir bench_out/judged/glm-5.1_run1
    python scripts/plot_results.py --results-dir bench_out/judge_results_v4   # legacy
    python scripts/plot_results.py --category-file configs/submit76/curated_questions_categorized.json
"""
import argparse
import json, os, glob, statistics
from collections import defaultdict
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch


def find_results_dir():
    """Auto-detect the most recent judged results directory."""
    judged_base = "bench_out/judged"
    if os.path.isdir(judged_base):
        subdirs = sorted(
            [os.path.join(judged_base, d) for d in os.listdir(judged_base)
             if os.path.isdir(os.path.join(judged_base, d))],
            key=os.path.getmtime, reverse=True,
        )
        if subdirs:
            return subdirs[0]
    # Legacy fallback
    legacy = "bench_out/judge_results_v4"
    if os.path.isdir(legacy):
        return legacy
    return None


def load_configs_from_judged(results_dir):
    """Load per-config question scores from a judged results directory.

    Supports two formats:
      - NEW: each JSON has benchmarking_results[].single_question_results
      - LEGACY: each JSON is a flat {qkey: {dim: score}} or has a "results" key
    """
    configs = defaultdict(dict)
    for fpath in sorted(glob.glob(os.path.join(results_dir, "*.json"))):
        fname = os.path.basename(fpath)
        if fname.startswith("."):
            continue
        with open(fpath) as f:
            data = json.load(f)

        # NEW format: benchmarking_results
        if "benchmarking_results" in data:
            for cfg in data["benchmarking_results"]:
                eval_name = cfg.get("eval_name", fname.replace(".json", ""))
                sqr = cfg.get("single_question_results", {})
                for qkey, qdata in sqr.items():
                    scores = {}
                    for dim in DIMS_ALL:
                        val = qdata.get(f"llm_judge_{dim}")
                        if val is not None and str(val).lower() not in ("null", "none"):
                            try:
                                scores[dim] = float(val)
                            except (ValueError, TypeError):
                                pass
                    if scores:
                        configs[eval_name][qkey] = scores
        else:
            # LEGACY format
            config_name = fname.replace(".json", "").rsplit("_batch", 1)[0]
            flat = data.get("results", data) if isinstance(data.get("results"), dict) else data
            for qkey, scores in flat.items():
                if isinstance(scores, dict) and "relevance" in scores:
                    configs[config_name][qkey] = scores

    return configs


def load_categories(category_file, configs):
    """Build qkey -> category mapping. Tries multiple strategies."""
    qkey_to_cat = {}

    if category_file and os.path.exists(category_file):
        with open(category_file) as f:
            ref_data = json.load(f)
        q_text_to_cat = {item["question"].strip(): item.get("category", "unknown") for item in ref_data}

        # Try to map qkey -> question text from config data
        qkey_to_text = {}
        for cn_scores in configs.values():
            for qk, sc in cn_scores.items():
                if "question" in sc:
                    qkey_to_text[qk] = sc["question"].strip()

        # Also try legacy batch splits
        for fpath in sorted(glob.glob("bench_out/judge_batches_v4/splits/*.json")):
            with open(fpath) as f:
                bd = json.load(f)
            for q in bd.get("questions", []):
                qkey_to_text[q["qkey"]] = q["question"].strip()

        for qk, qtxt in qkey_to_text.items():
            qkey_to_cat[qk] = q_text_to_cat.get(qtxt, "unknown")

    return qkey_to_cat


DIMS_ALL = ["relevance", "completeness", "specificity", "helpfulness", "source_faithfulness"]
DIMS_CORE = DIMS_ALL[:4]

ARCH_COLORS = {
    "bare-llm": "#e74c3c",
    "rag-only": "#3498db",
    "copilot": "#2ecc71",
    "compops": "#9b59b6",
}

CAT_LABELS = {
    "data_query": "Data Query",
    "debugging": "Debugging",
    "exploratory": "Exploratory",
    "factual_lookup": "Factual",
    "jira_investigation": "JIRA",
    "procedural": "Procedural",
}


def get_color(cn):
    for prefix, color in ARCH_COLORS.items():
        if cn.startswith(prefix):
            return color
    return "#95a5a6"


def detect_architecture(cn):
    """Detect pipeline architecture from config name."""
    for prefix in ["bare-llm", "rag-only", "copilot-no-tools", "copilot", "compops-no-tools", "compops"]:
        if cn.startswith(prefix):
            return prefix
    return "other"


def detect_model(cn):
    """Try to extract model identifier from config name suffix."""
    # e.g. "copilot_qwen3-32b" or "bare-llm-120b" or "compops_gemma4-26b"
    parts = cn.replace("_", "-").split("-")
    # Look for common model identifiers
    for pattern in ["120b", "32b", "26b", "gpt5", "gemma4", "qwen3", "gpt-oss"]:
        if pattern in cn:
            # Extract the model portion
            idx = cn.find(pattern)
            # Walk back to find start of model name
            start = max(0, cn.rfind("-", 0, idx))
            if start == 0 and idx > 0:
                start = max(0, cn.rfind("_", 0, idx))
            return cn[start:].lstrip("-_")
    return "unknown"


def compute_stats(configs):
    """Compute per-config score-by-dimension lists, means, and avg4."""
    config_sbd = {}
    config_means = {}
    config_avg4 = {}
    for cn in configs:
        sbd = defaultdict(list)
        for qk, sc in configs[cn].items():
            for dim in DIMS_ALL:
                val = sc.get(dim)
                if val is not None and str(val).lower() not in ("null", "none"):
                    try:
                        sbd[dim].append(float(val))
                    except (ValueError, TypeError):
                        pass
        config_sbd[cn] = sbd
        config_means[cn] = {d: (statistics.mean(sbd[d]) if sbd[d] else None) for d in DIMS_ALL}
        core = [config_means[cn][d] for d in DIMS_CORE if config_means[cn][d] is not None]
        config_avg4[cn] = statistics.mean(core) if core else 0
    return config_sbd, config_means, config_avg4


def main():
    parser = argparse.ArgumentParser(description="Generate CHEP 2026 evaluation plots")
    parser.add_argument("--results-dir", type=str, default=None,
                        help="Directory containing judged result JSON files")
    parser.add_argument("--category-file", type=str,
                        default="configs/submit76/curated_questions_categorized.json",
                        help="Path to curated_questions_categorized.json")
    parser.add_argument("--output-dir", type=str, default="bench_out/plots",
                        help="Output directory for plot PNGs")
    args = parser.parse_args()

    results_dir = args.results_dir or find_results_dir()
    if not results_dir or not os.path.isdir(results_dir):
        print(f"ERROR: Results directory not found. Tried: {results_dir}")
        print("Use --results-dir to specify the path.")
        return

    print(f"Loading results from: {results_dir}")
    configs = load_configs_from_judged(results_dir)
    if not configs:
        print("ERROR: No scored configs found.")
        return
    print(f"Found {len(configs)} configs: {sorted(configs.keys())}")

    config_sbd, config_means, config_avg4 = compute_stats(configs)
    ranked = sorted(configs, key=lambda x: config_avg4[x], reverse=True)

    # Categories (optional — plots 5-6 skip if unavailable)
    qkey_to_cat = load_categories(args.category_file, configs)
    categories = sorted(set(qkey_to_cat.values())) if qkey_to_cat else []
    has_categories = len(categories) > 1

    outdir = args.output_dir
    os.makedirs(outdir, exist_ok=True)
    plt.rcParams.update({'font.size': 11, 'figure.dpi': 150, 'savefig.bbox': 'tight', 'savefig.pad_inches': 0.15})

    plot_num = 0

    # ═══════════════════════════════════════════════════════
    # PLOT 1: Overall Avg4 bar chart (horizontal)
    # ═══════════════════════════════════════════════════════
    plot_num += 1
    fig, ax = plt.subplots(figsize=(10, max(4, len(ranked) * 0.4)))
    ranked_rev = list(reversed(ranked))
    colors = [get_color(cn) for cn in ranked_rev]
    ax.barh(range(len(ranked_rev)), [config_avg4[cn] for cn in ranked_rev], color=colors, edgecolor='white', linewidth=0.5)
    ax.set_yticks(range(len(ranked_rev)))
    ax.set_yticklabels(ranked_rev, fontsize=10)
    ax.set_xlim(0, 5)
    ax.set_xlabel("Avg4 Score (mean of relevance, completeness, specificity, helpfulness)")
    ax.set_title("CMS CompOps AI — Overall Quality Score by Configuration", fontsize=13, fontweight='bold')
    for i, cn in enumerate(ranked_rev):
        ax.text(config_avg4[cn] + 0.05, i, f"{config_avg4[cn]:.2f}", va='center', fontsize=9, fontweight='bold')
    ax.axvline(x=3, color='gray', linestyle='--', alpha=0.3, linewidth=0.8)
    # Build legend from architectures actually present
    present_archs = set()
    for cn in ranked:
        for prefix in ARCH_COLORS:
            if cn.startswith(prefix):
                present_archs.add(prefix)
    legend_elements = [Patch(facecolor=ARCH_COLORS[a], label=a) for a in ARCH_COLORS if a in present_archs]
    if legend_elements:
        ax.legend(handles=legend_elements, loc='lower right', fontsize=9)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    fig.savefig(f"{outdir}/01_overall_avg4.png")
    plt.close()
    print(f"{plot_num}/7 overall_avg4")

    # ═══════════════════════════════════════════════════════
    # PLOT 2: Grouped bar — all 4 core dimensions per config
    # ═══════════════════════════════════════════════════════
    plot_num += 1
    fig, ax = plt.subplots(figsize=(max(10, len(ranked) * 0.8), 6))
    x = np.arange(len(ranked))
    width = 0.2
    dim_colors = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12']
    for i, dim in enumerate(DIMS_CORE):
        vals = [config_means[cn][dim] or 0 for cn in ranked]
        ax.bar(x + i*width - 1.5*width, vals, width, label=dim.capitalize(), color=dim_colors[i], alpha=0.85)
    ax.set_xticks(x)
    ax.set_xticklabels(ranked, rotation=35, ha='right', fontsize=9)
    ax.set_ylabel("Mean Score (1-5)")
    ax.set_ylim(0, 5.2)
    ax.set_title("Score Breakdown by Dimension", fontsize=13, fontweight='bold')
    ax.legend(fontsize=9)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)
    fig.savefig(f"{outdir}/02_dimensions_grouped.png")
    plt.close()
    print(f"{plot_num}/7 dimensions_grouped")

    # ═══════════════════════════════════════════════════════
    # PLOT 3: Architecture comparison (dynamic)
    # ═══════════════════════════════════════════════════════
    plot_num += 1
    arch_groups = defaultdict(list)
    for cn in ranked:
        arch = detect_architecture(cn)
        arch_groups[arch].append(cn)

    # Order architectures by increasing complexity
    arch_order_pref = ["bare-llm", "rag-only", "copilot-no-tools", "copilot", "compops-no-tools", "compops", "other"]
    archs_present = [a for a in arch_order_pref if a in arch_groups]

    if len(archs_present) >= 2:
        fig, ax = plt.subplots(figsize=(max(8, len(archs_present) * 1.5), 6))
        arch_avgs = []
        arch_stds = []
        arch_points = []
        for arch in archs_present:
            vals = [config_avg4[c] for c in arch_groups[arch]]
            arch_avgs.append(statistics.mean(vals))
            arch_stds.append(statistics.stdev(vals) if len(vals) > 1 else 0)
            arch_points.append(vals)
        default_colors = ['#e74c3c', '#3498db', '#2ecc71', '#9b59b6', '#f39c12', '#1abc9c', '#95a5a6']
        colors_arch = default_colors[:len(archs_present)]
        ax.bar(range(len(archs_present)), arch_avgs, color=colors_arch, alpha=0.8, edgecolor='white', linewidth=0.5)
        ax.errorbar(range(len(archs_present)), arch_avgs, yerr=arch_stds, fmt='none', ecolor='black', capsize=5, linewidth=1.5)
        for i, pts in enumerate(arch_points):
            ax.scatter([i]*len(pts), pts, color='black', s=40, zorder=5, alpha=0.7)
            for p in pts:
                ax.text(i + 0.15, p, f"{p:.2f}", fontsize=8, va='center')
        ax.set_xticks(range(len(archs_present)))
        ax.set_xticklabels(archs_present, fontsize=10)
        ax.set_ylabel("Avg4 Score")
        ax.set_ylim(0, 5.2)
        ax.set_title("Architecture Comparison (mean +/- std across models)", fontsize=13, fontweight='bold')
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        fig.savefig(f"{outdir}/03_architecture_comparison.png")
        plt.close()
        print(f"{plot_num}/7 architecture_comparison")
    else:
        print(f"{plot_num}/7 architecture_comparison — SKIPPED (need >= 2 architectures)")

    # ═══════════════════════════════════════════════════════
    # PLOT 4: Pipeline uplift by model (dynamic)
    # ═══════════════════════════════════════════════════════
    plot_num += 1
    # Group configs by model, then by architecture
    model_arch = defaultdict(dict)  # model -> {arch: config_name}
    for cn in ranked:
        arch = detect_architecture(cn)
        model = detect_model(cn)
        if model != "unknown":
            model_arch[model][arch] = cn

    if len(model_arch) >= 2:
        fig, ax = plt.subplots(figsize=(10, 6))
        all_archs = sorted(set(a for m in model_arch.values() for a in m))
        # Reorder by complexity
        arch_sort = {a: i for i, a in enumerate(arch_order_pref)}
        all_archs.sort(key=lambda a: arch_sort.get(a, 99))
        markers = ['o', 's', 'D', '^', 'v', 'P', 'X']
        for j, (model, pipes) in enumerate(sorted(model_arch.items())):
            xs, ys = [], []
            for i, arch in enumerate(all_archs):
                if arch in pipes and pipes[arch] in config_avg4:
                    xs.append(i)
                    ys.append(config_avg4[pipes[arch]])
            mk = markers[j % len(markers)]
            ax.plot(xs, ys, marker=mk, markersize=10, linewidth=2.5, label=model, alpha=0.85)
            for x, y in zip(xs, ys):
                ax.text(x + 0.08, y + 0.06, f"{y:.2f}", fontsize=9)
        ax.set_xticks(range(len(all_archs)))
        ax.set_xticklabels(all_archs, fontsize=11)
        ax.set_ylabel("Avg4 Score")
        ax.set_title("Pipeline Uplift by Model", fontsize=13, fontweight='bold')
        ax.legend(fontsize=10)
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.grid(axis='y', alpha=0.3)
        fig.savefig(f"{outdir}/04_pipeline_uplift.png")
        plt.close()
        print(f"{plot_num}/7 pipeline_uplift")
    else:
        print(f"{plot_num}/7 pipeline_uplift — SKIPPED (need >= 2 models)")

    # ═══════════════════════════════════════════════════════
    # PLOT 5: Category heatmap
    # ═══════════════════════════════════════════════════════
    plot_num += 1
    if has_categories:
        cat_avg4 = {}
        for cn in ranked:
            cat_scores = defaultdict(list)
            for qk, sc in configs[cn].items():
                cat = qkey_to_cat.get(qk, "unknown")
                core_vals = []
                for d in DIMS_CORE:
                    val = sc.get(d)
                    if val is not None and str(val).lower() not in ("null", "none"):
                        try:
                            core_vals.append(float(val))
                        except (ValueError, TypeError):
                            pass
                if core_vals:
                    cat_scores[cat].append(statistics.mean(core_vals))
            cat_avg4[cn] = {cat: (statistics.mean(cat_scores[cat]) if cat_scores[cat] else 0) for cat in categories}

        matrix = np.array([[cat_avg4[cn][cat] for cat in categories] for cn in ranked])
        fig, ax = plt.subplots(figsize=(max(8, len(categories) * 1.5), max(5, len(ranked) * 0.5)))
        im = ax.imshow(matrix, cmap='RdYlGn', aspect='auto', vmin=1, vmax=5)
        ax.set_xticks(range(len(categories)))
        ax.set_xticklabels([CAT_LABELS.get(c, c) for c in categories], fontsize=10, rotation=30, ha='right')
        ax.set_yticks(range(len(ranked)))
        ax.set_yticklabels(ranked, fontsize=10)
        for i in range(len(ranked)):
            for j in range(len(categories)):
                val = matrix[i, j]
                color = 'white' if val < 2.5 or val > 4.2 else 'black'
                ax.text(j, i, f"{val:.1f}", ha='center', va='center', fontsize=9, fontweight='bold', color=color)
        plt.colorbar(im, ax=ax, shrink=0.8, label='Avg4 Score')
        ax.set_title("Avg4 by Configuration x Question Category", fontsize=13, fontweight='bold')
        fig.savefig(f"{outdir}/05_category_heatmap.png")
        plt.close()
        print(f"{plot_num}/7 category_heatmap")
    else:
        print(f"{plot_num}/7 category_heatmap — SKIPPED (no category data)")

    # ═══════════════════════════════════════════════════════
    # PLOT 6: Category radar for top configs
    # ═══════════════════════════════════════════════════════
    plot_num += 1
    if has_categories and len(ranked) >= 3:
        top_n = min(5, len(ranked))
        top_configs = ranked[:top_n]
        top_default_colors = ['#f39c12', '#2ecc71', '#9b59b6', '#3498db', '#e74c3c']
        top_colors = top_default_colors[:top_n]
        cat_short = [CAT_LABELS.get(c, c) for c in categories]
        angles = np.linspace(0, 2*np.pi, len(categories), endpoint=False).tolist()
        angles += angles[:1]

        fig, ax = plt.subplots(figsize=(9, 9), subplot_kw=dict(polar=True))
        for cn, color in zip(top_configs, top_colors):
            vals = [cat_avg4[cn][cat] for cat in categories]
            vals += vals[:1]
            ax.plot(angles, vals, 'o-', linewidth=2, label=cn, color=color, markersize=5)
            ax.fill(angles, vals, alpha=0.08, color=color)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(cat_short, fontsize=10)
        ax.set_ylim(0, 5)
        ax.set_yticks([1, 2, 3, 4, 5])
        ax.set_yticklabels(['1', '2', '3', '4', '5'], fontsize=8)
        ax.set_title("Category Profile — Top Configurations", fontsize=13, fontweight='bold', y=1.08)
        ax.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1), fontsize=9)
        fig.savefig(f"{outdir}/06_category_radar.png")
        plt.close()
        print(f"{plot_num}/7 category_radar")
    else:
        print(f"{plot_num}/7 category_radar — SKIPPED (need categories + >= 3 configs)")

    # ═══════════════════════════════════════════════════════
    # PLOT 7: Score distribution boxplots (top configs)
    # ═══════════════════════════════════════════════════════
    plot_num += 1
    top_n = min(5, len(ranked))
    selected = ranked[:top_n]
    sel_default_colors = ['#f39c12', '#2ecc71', '#3498db', '#9b59b6', '#e74c3c']
    sel_colors = sel_default_colors[:top_n]

    fig, axes = plt.subplots(1, len(DIMS_CORE), figsize=(4 * len(DIMS_CORE), 5), sharey=True)
    if len(DIMS_CORE) == 1:
        axes = [axes]
    for di, dim in enumerate(DIMS_CORE):
        ax = axes[di]
        box_data = []
        labels = []
        for cn in selected:
            box_data.append(config_sbd[cn].get(dim, []))
            # Abbreviate long names
            short = cn
            for prefix in ["copilot-no-tools-", "copilot-", "rag-only-", "compops-no-tools-", "compops-", "bare-llm-"]:
                if cn.startswith(prefix):
                    abbr = {"copilot-no-tools-": "CPNT-", "copilot-": "CP-", "rag-only-": "RAG-",
                            "compops-no-tools-": "CONT-", "compops-": "CO-", "bare-llm-": "BL-"}
                    short = abbr[prefix] + cn[len(prefix):]
                    break
            labels.append(short)
        if any(box_data):
            bp = ax.boxplot(box_data, patch_artist=True, widths=0.6, showmeans=True,
                            meanprops=dict(marker='D', markerfacecolor='black', markersize=5))
            for patch, color in zip(bp['boxes'], sel_colors):
                patch.set_facecolor(color)
                patch.set_alpha(0.6)
        ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
        ax.set_title(dim.capitalize(), fontsize=11, fontweight='bold')
        ax.set_ylim(0.5, 5.5)
        ax.set_yticks([1, 2, 3, 4, 5])
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
    axes[0].set_ylabel("Score (1-5)")
    fig.suptitle("Score Distributions by Dimension (top configs)", fontsize=13, fontweight='bold', y=1.02)
    fig.tight_layout()
    fig.savefig(f"{outdir}/07_score_distributions.png")
    plt.close()
    print(f"{plot_num}/7 score_distributions")

    print(f"\nAll plots saved to {outdir}/")
    for f in sorted(os.listdir(outdir)):
        if f.endswith('.png'):
            sz = os.path.getsize(os.path.join(outdir, f))
            print(f"  {f}  ({sz//1024}KB)")


if __name__ == "__main__":
    main()
