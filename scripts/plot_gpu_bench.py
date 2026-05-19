#!/usr/bin/env python3
"""Plot GPU utilization from benchmark CSV data."""
import csv
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np

CSV_PATHS = ["/tmp/gpu_bench.csv", "/tmp/gpu_bench_gemma4_26b.csv"]
OUTPUT_PATH = "/tmp/gpu_bench_plot.png"

# Load data from all CSVs
rows = []
for csv_path in CSV_PATHS:
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({
                "time": float(r["time"]),
                "model": r["model"],
                "gpu": int(r["gpu"]),
                "gpu_util": float(r["gpu_util"]),
                "mem_util": float(r["mem_util"]),
                "mem_used_mib": float(r["mem_used_mib"]),
                "mem_total_mib": float(r["mem_total_mib"]),
                "power_w": float(r["power_w"]),
            })

models = ["qwen3:32b", "gemma4:26b", "gemma4:31b", "gpt-oss:120b"]
gpu_colors = {0: "#e74c3c", 1: "#2ecc71", 2: "#3498db", 3: "#f39c12"}
gpu_labels = {i: f"GPU {i}" for i in range(4)}

# Benchmark results for annotation
bench_results = {
    "qwen3:32b": {"gen_tok_s": 28.5, "prompt_tok_s": 278.1, "wall_s": 11.1, "load_s": 0.18},
    "gemma4:26b": {"gen_tok_s": 87.1, "prompt_tok_s": 248.3, "wall_s": 15.1, "load_s": 10.9},
    "gemma4:31b": {"gen_tok_s": 3.1, "prompt_tok_s": 16.3, "wall_s": 123.8, "load_s": 22.65},
    "gpt-oss:120b": {"gen_tok_s": 80.1, "prompt_tok_s": 282.1, "wall_s": 19.0, "load_s": 14.55},
}

fig = plt.figure(figsize=(18, 18))
fig.suptitle("GPU Utilization During Ollama Inference — submit75 (4× V100-SXM2-32GB)",
             fontsize=16, fontweight="bold", y=0.98)

gs = gridspec.GridSpec(4, 2, hspace=0.35, wspace=0.25, top=0.93, bottom=0.04)

for idx, model in enumerate(models):
    model_rows = [r for r in rows if r["model"] == model]
    if not model_rows:
        continue

    # GPU Utilization subplot (left column)
    ax_util = fig.add_subplot(gs[idx, 0])
    for gpu_id in range(4):
        gpu_data = [r for r in model_rows if r["gpu"] == gpu_id]
        if gpu_data:
            times = [r["time"] for r in gpu_data]
            utils = [r["gpu_util"] for r in gpu_data]
            ax_util.plot(times, utils, color=gpu_colors[gpu_id],
                        label=gpu_labels[gpu_id], alpha=0.8, linewidth=1.2)

    info = bench_results[model]
    ax_util.set_title(f"{model}  —  {info['gen_tok_s']} tok/s gen, {info['wall_s']}s wall",
                      fontsize=12, fontweight="bold")
    ax_util.set_ylabel("GPU Utilization (%)")
    ax_util.set_ylim(-5, 105)
    ax_util.set_xlabel("Time (s)")
    ax_util.legend(loc="upper right", fontsize=8, ncol=2)
    ax_util.grid(True, alpha=0.3)
    ax_util.axhline(y=0, color="gray", linewidth=0.5)

    # Add load time marker
    if info["load_s"] > 1:
        ax_util.axvline(x=info["load_s"], color="gray", linestyle="--",
                       alpha=0.5, linewidth=1)
        ax_util.annotate("model loaded", xy=(info["load_s"], 95),
                         fontsize=8, color="gray", ha="left")

    # Memory Usage subplot (right column)
    ax_mem = fig.add_subplot(gs[idx, 1])
    for gpu_id in range(4):
        gpu_data = [r for r in model_rows if r["gpu"] == gpu_id]
        if gpu_data:
            times = [r["time"] for r in gpu_data]
            mem_gb = [r["mem_used_mib"] / 1024 for r in gpu_data]
            ax_mem.plot(times, mem_gb, color=gpu_colors[gpu_id],
                       label=gpu_labels[gpu_id], alpha=0.8, linewidth=1.2)

    ax_mem.axhline(y=32, color="gray", linestyle=":", alpha=0.5, linewidth=1)
    ax_mem.annotate("32 GB limit", xy=(0, 32.2), fontsize=8, color="gray")
    ax_mem.set_title(f"{model}  —  VRAM Usage", fontsize=12, fontweight="bold")
    ax_mem.set_ylabel("VRAM Used (GB)")
    ax_mem.set_ylim(0, 35)
    ax_mem.set_xlabel("Time (s)")
    ax_mem.legend(loc="upper right", fontsize=8, ncol=2)
    ax_mem.grid(True, alpha=0.3)

plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches="tight", facecolor="white")
print(f"Plot saved to {OUTPUT_PATH}")
plt.close()
