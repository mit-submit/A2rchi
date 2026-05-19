#!/usr/bin/env python3
"""Benchmark Ollama models while logging GPU utilization at high frequency."""
import csv
import json
import subprocess
import threading
import time
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/generate"
PROMPT = "Explain the CMS experiment at CERN and its role in discovering the Higgs boson. Include details about the detector subsystems, the discovery timeline, and the statistical significance of the discovery."
MODELS = ["qwen3:32b", "gemma4:31b", "gpt-oss:120b"]
POLL_INTERVAL = 0.25  # seconds between GPU samples
OUTPUT_CSV = "/tmp/gpu_bench.csv"
OUTPUT_JSON = "/tmp/gpu_bench_results.json"


def poll_gpu(stop_event, rows, model_label):
    """Poll nvidia-smi and append rows until stop_event is set."""
    t0 = time.time()
    while not stop_event.is_set():
        try:
            out = subprocess.check_output(
                ["nvidia-smi",
                 "--query-gpu=index,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw",
                 "--format=csv,noheader,nounits"],
                text=True, timeout=5
            )
            ts = time.time() - t0
            for line in out.strip().split("\n"):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 6:
                    rows.append({
                        "time": round(ts, 3),
                        "model": model_label,
                        "gpu": int(parts[0]),
                        "gpu_util": float(parts[1]),
                        "mem_util": float(parts[2]),
                        "mem_used_mib": float(parts[3]),
                        "mem_total_mib": float(parts[4]),
                        "power_w": float(parts[5]),
                    })
        except Exception as e:
            print(f"  poll error: {e}")
        stop_event.wait(POLL_INTERVAL)


def run_inference(model, prompt, num_predict=300):
    """Run a single Ollama inference and return timing dict."""
    data = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": num_predict},
    }).encode()
    req = urllib.request.Request(OLLAMA_URL, data=data,
                                headers={"Content-Type": "application/json"})
    t0 = time.time()
    resp = urllib.request.urlopen(req, timeout=600)
    result = json.loads(resp.read())
    elapsed = time.time() - t0

    ec = result.get("eval_count", 0)
    ed = result.get("eval_duration", 0)
    pc = result.get("prompt_eval_count", 0)
    pd = result.get("prompt_eval_duration", 0)
    ld = result.get("load_duration", 0)

    return {
        "model": model,
        "load_s": round(ld / 1e9, 2),
        "prompt_tokens": pc,
        "prompt_tok_s": round(pc / (pd / 1e9), 1) if pd else 0,
        "gen_tokens": ec,
        "gen_tok_s": round(ec / (ed / 1e9), 1) if ed else 0,
        "wall_s": round(elapsed, 1),
        "response_chars": len(result.get("response", "")),
    }


def main():
    all_rows = []
    results = []

    for model in MODELS:
        print(f"\n{'='*60}")
        print(f"Benchmarking: {model}")
        print(f"{'='*60}")

        # Start GPU polling
        stop = threading.Event()
        gpu_rows = []
        poller = threading.Thread(target=poll_gpu, args=(stop, gpu_rows, model))
        poller.start()

        try:
            info = run_inference(model, PROMPT)
            results.append(info)
            print(f"  Load: {info['load_s']}s")
            print(f"  Prompt: {info['prompt_tokens']} tok @ {info['prompt_tok_s']} tok/s")
            print(f"  Gen: {info['gen_tokens']} tok @ {info['gen_tok_s']} tok/s")
            print(f"  Wall: {info['wall_s']}s")
        except Exception as e:
            print(f"  ERROR: {e}")
            results.append({"model": model, "error": str(e)})

        # Stop polling
        stop.set()
        poller.join(timeout=5)
        all_rows.extend(gpu_rows)
        print(f"  GPU samples: {len(gpu_rows)}")

    # Write CSV
    if all_rows:
        with open(OUTPUT_CSV, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_rows[0].keys()))
            w.writeheader()
            w.writerows(all_rows)
        print(f"\nGPU data written to {OUTPUT_CSV} ({len(all_rows)} rows)")

    # Write results JSON
    with open(OUTPUT_JSON, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Results written to {OUTPUT_JSON}")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    for r in results:
        if "error" in r:
            print(f"  {r['model']}: ERROR - {r['error']}")
        else:
            print(f"  {r['model']}: {r['gen_tok_s']} tok/s (gen), {r['prompt_tok_s']} tok/s (prompt), {r['wall_s']}s wall")


if __name__ == "__main__":
    main()
