#!/usr/bin/env python3
"""Submit75 model benchmarking: tok/s, thinking toggle, GPU util, context size.

Runs three phases against the local Ollama daemon:

1. **tok_s**: a short-context tok/s measurement for each (model, thinking) pair.
2. **gpu**: one representative inference per model with nvidia-smi sampled at
   0.5 s while the request is in flight, showing which GPUs were used.
3. **context**: a near-256K prompt for each model to confirm full-context
   ingestion works (or not, and how it fails).

All phases write structured JSON to stdout. The driver script on the client
side parses it. Uses the stdlib only so it runs on any Python 3.8+ on host.

Usage on submit75:

    python3 ~/archi/scripts/submit75/bench_models.py --phase tok_s > out.json
    python3 ~/archi/scripts/submit75/bench_models.py --phase context --only qwen3.5:27b
    python3 ~/archi/scripts/submit75/bench_models.py --phase all --tok-s-predict 500

The context phase may take 10+ minutes per model because prompt ingestion of
~200K tokens is slow. Use --phase tok_s first.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

DEFAULT_MODELS = [
    "gemma4:26b",
    "qwen3.5:27b",
    "qwen3.5:122b-a10b",
]


def chat(
    model: str,
    user_prompt: str,
    *,
    system: Optional[str] = None,
    think: Optional[bool] = None,
    options: Optional[Dict[str, Any]] = None,
    timeout: float = 1800,
) -> tuple[Dict[str, Any], float]:
    """Call `/api/chat` with a messages array and optional top-level `think` flag.

    `think` is the native Ollama thinking toggle (Ollama >= 0.6). Pass `None` to
    leave it unset (model default). Pass `False` to explicitly disable thinking,
    `True` to explicitly enable it. `system` is sent as a proper system message.
    Using `/api/chat` (not `/api/generate`) ensures the model's chat template is
    applied — gemma4 in particular requires the chat template to produce a
    non-empty response.
    """
    messages: List[Dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_prompt})

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": options or {},
        "keep_alive": "5m",
    }
    if think is not None:
        body["think"] = think

    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return data, time.time() - start
    except urllib.error.HTTPError as e:
        try:
            payload = e.read().decode(errors="replace")
        except Exception:
            payload = ""
        return {"error": f"HTTP {e.code}: {payload[:500]}"}, time.time() - start
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}, time.time() - start


def _extract_chat_fields(result: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize `/api/chat` response so downstream code looks the same as `/api/generate`."""
    msg = result.get("message") or {}
    return {
        "response": msg.get("content") or "",
        "thinking": msg.get("thinking") or "",
        "role": msg.get("role"),
    }


def unload_model(model: str) -> None:
    """Force Ollama to unload a model from GPU memory."""
    body = {"model": model, "messages": [], "keep_alive": 0}
    try:
        req = urllib.request.Request(
            f"{OLLAMA_URL}/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=60).read()
    except Exception:
        pass


def tok_s(result: Dict[str, Any]) -> float:
    eval_count = result.get("eval_count") or 0
    eval_duration = result.get("eval_duration") or 0
    if not eval_count or not eval_duration:
        return 0.0
    return round(eval_count / (eval_duration / 1e9), 2)


def prompt_tok_s(result: Dict[str, Any]) -> float:
    p = result.get("prompt_eval_count") or 0
    d = result.get("prompt_eval_duration") or 0
    if not p or not d:
        return 0.0
    return round(p / (d / 1e9), 2)


class GpuSampler(threading.Thread):
    def __init__(self, interval: float = 0.5):
        super().__init__(daemon=True)
        self.interval = interval
        self.samples: List[Dict[str, Any]] = []
        self._stop_evt = threading.Event()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                out = subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=index,utilization.gpu,memory.used,memory.total",
                        "--format=csv,noheader,nounits",
                    ],
                    timeout=5,
                ).decode()
                gpus = []
                for line in out.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 4:
                        gpus.append(
                            {
                                "idx": int(parts[0]),
                                "util": int(parts[1]),
                                "mem_mb": int(parts[2]),
                                "mem_total_mb": int(parts[3]),
                            }
                        )
                self.samples.append({"t": time.time(), "gpus": gpus})
            except Exception as e:
                self.samples.append({"error": str(e)})
            if self._stop_evt.wait(self.interval):
                break

    def stop_sampling(self) -> None:
        self._stop_evt.set()

    def summary(self) -> Dict[str, Any]:
        if not self.samples:
            return {"samples": 0}
        per_gpu: Dict[int, Dict[str, Any]] = {}
        for s in self.samples:
            for g in s.get("gpus", []):
                idx = g["idx"]
                p = per_gpu.setdefault(
                    idx,
                    {
                        "idx": idx,
                        "util_max": 0,
                        "util_avg": 0.0,
                        "util_count": 0,
                        "mem_max_mb": 0,
                        "mem_total_mb": g["mem_total_mb"],
                    },
                )
                p["util_max"] = max(p["util_max"], g["util"])
                p["util_avg"] += g["util"]
                p["util_count"] += 1
                p["mem_max_mb"] = max(p["mem_max_mb"], g["mem_mb"])
        for p in per_gpu.values():
            if p["util_count"]:
                p["util_avg"] = round(p["util_avg"] / p["util_count"], 1)
            del p["util_count"]
        return {
            "samples": len(self.samples),
            "per_gpu": sorted(per_gpu.values(), key=lambda p: p["idx"]),
        }


def run_tok_s(model: str, thinking_on: bool, num_predict: int) -> Dict[str, Any]:
    prompt = (
        "Count from 1 to 30 and for each integer state one interesting mathematical "
        "or physical property. Be concise. One short sentence per number."
    )
    options = {
        "num_predict": num_predict,
        "temperature": 0.0,
        "num_ctx": 8192,
    }

    sampler = GpuSampler()
    sampler.start()
    result, wall = chat(model, prompt, think=thinking_on, options=options, timeout=600)
    sampler.stop_sampling()
    sampler.join(timeout=3)

    fields = _extract_chat_fields(result)
    resp = fields["response"].strip()
    thinking = fields["thinking"].strip()
    return {
        "model": model,
        "thinking_mode": "on" if thinking_on else "off",
        "wall_s": round(wall, 2),
        "tok_s_eval": tok_s(result),
        "tok_s_prompt": prompt_tok_s(result),
        "prompt_tokens": result.get("prompt_eval_count"),
        "eval_tokens": result.get("eval_count"),
        "load_ms": (result.get("load_duration") or 0) // 1_000_000,
        "prompt_eval_ms": (result.get("prompt_eval_duration") or 0) // 1_000_000,
        "eval_ms": (result.get("eval_duration") or 0) // 1_000_000,
        "total_ms": (result.get("total_duration") or 0) // 1_000_000,
        "thinking_len": len(thinking),
        "thinking_preview": thinking[:120],
        "response_len": len(resp),
        "response_preview": resp[:120],
        "error": result.get("error"),
        "gpu": sampler.summary(),
    }


def run_context_probe(model: str, num_predict: int, target_tokens: int) -> Dict[str, Any]:
    """Send a large prompt near `target_tokens` tokens and see if the model handles it."""
    filler_unit = "The scientific method involves observation, hypothesis, experiment, and analysis. "
    # ~80 chars ≈ 20 tokens per unit
    n_copies = max(1, (target_tokens // 20) - 10)
    filler = filler_unit * n_copies
    prompt = (
        filler
        + "\n\nYou were just given a lot of repetitive text. In one short sentence, what topic does it describe?"
    )

    options = {
        "num_predict": num_predict,
        "temperature": 0.0,
        "num_ctx": 262144,
    }

    sampler = GpuSampler()
    sampler.start()
    result, wall = chat(model, prompt, think=False, options=options, timeout=2400)
    sampler.stop_sampling()
    sampler.join(timeout=3)

    fields = _extract_chat_fields(result)
    return {
        "model": model,
        "test": "near_256k",
        "target_tokens": target_tokens,
        "prompt_chars": len(prompt),
        "wall_s": round(wall, 2),
        "tok_s_eval": tok_s(result),
        "tok_s_prompt": prompt_tok_s(result),
        "prompt_tokens": result.get("prompt_eval_count"),
        "eval_tokens": result.get("eval_count"),
        "load_ms": (result.get("load_duration") or 0) // 1_000_000,
        "prompt_eval_ms": (result.get("prompt_eval_duration") or 0) // 1_000_000,
        "eval_ms": (result.get("eval_duration") or 0) // 1_000_000,
        "total_ms": (result.get("total_duration") or 0) // 1_000_000,
        "response_preview": fields["response"][:200],
        "error": result.get("error"),
        "gpu": sampler.summary(),
    }


def run_ctx_sweep(model: str, num_ctx_values: List[int], num_predict: int) -> List[Dict[str, Any]]:
    """For each `num_ctx`, load the model with that context size and run a
    short prompt. Measures peak GPU memory to reveal KV-cache pre-allocation.
    """
    prompt = "Write one short sentence about physics."
    results: List[Dict[str, Any]] = []
    for nc in num_ctx_values:
        # Force a reload at this num_ctx by unloading first. Ollama reloads the
        # model if the requested num_ctx differs from what's currently loaded.
        unload_model(model)
        time.sleep(1)
        options = {
            "num_predict": num_predict,
            "temperature": 0.0,
            "num_ctx": nc,
        }
        sampler = GpuSampler(interval=0.25)
        sampler.start()
        result, wall = chat(model, prompt, think=False, options=options, timeout=600)
        sampler.stop_sampling()
        sampler.join(timeout=3)

        gpu_summary = sampler.summary()
        total_mem_mb = sum(
            (g.get("mem_max_mb") or 0) for g in gpu_summary.get("per_gpu", [])
        )
        kv_cache_mb_approx = None  # caller can compute by diffing vs smallest num_ctx

        results.append(
            {
                "model": model,
                "num_ctx": nc,
                "wall_s": round(wall, 2),
                "tok_s_eval": tok_s(result),
                "prompt_tokens": result.get("prompt_eval_count"),
                "eval_tokens": result.get("eval_count"),
                "load_ms": (result.get("load_duration") or 0) // 1_000_000,
                "total_mem_mb": total_mem_mb,
                "gpus_with_mem": [
                    g["idx"]
                    for g in gpu_summary.get("per_gpu", [])
                    if (g.get("mem_max_mb") or 0) > 100
                ],
                "error": result.get("error"),
            }
        )
    return results


def run_parallel_test(
    model: str,
    n_concurrent: int,
    num_predict: int,
    *,
    timeout: float = 600,
) -> Dict[str, Any]:
    """Fire `n_concurrent` chat requests against `model` at the same time.

    Measures aggregate throughput vs serial. If the aggregate tok/s across
    all workers is close to `n_concurrent × single-stream tok/s`, Ollama is
    batching effectively; if it's close to single-stream tok/s alone, we are
    bottlenecked on sequential decoding.
    """
    prompt = (
        "List 15 diverse technical facts about the Large Hadron Collider. "
        "Be concise. One short sentence per fact."
    )
    options = {
        "num_predict": num_predict,
        "temperature": 0.0,
        "num_ctx": 8192,
    }

    results: List[Dict[str, Any]] = [None] * n_concurrent  # type: ignore
    per_worker_wall: List[float] = [0.0] * n_concurrent

    sampler = GpuSampler()
    sampler.start()

    def worker(idx: int) -> None:
        r, w = chat(model, prompt, think=False, options=options, timeout=timeout)
        results[idx] = r
        per_worker_wall[idx] = w

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n_concurrent)]
    wall_start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout + 30)
    aggregate_wall = time.time() - wall_start
    sampler.stop_sampling()
    sampler.join(timeout=3)

    eval_counts = [(r or {}).get("eval_count") or 0 for r in results]
    eval_durations_ns = [(r or {}).get("eval_duration") or 0 for r in results]
    errors = [(r or {}).get("error") for r in results]
    total_eval_tokens = sum(eval_counts)

    aggregate_tok_s = round(total_eval_tokens / aggregate_wall, 2) if aggregate_wall > 0 else 0.0
    # Per-worker tok/s from their own eval_duration (this is time the model spent
    # generating tokens for that worker, not wall clock).
    per_worker_tok_s = [
        round((ec / (ed / 1e9)), 2) if ec and ed else 0.0
        for ec, ed in zip(eval_counts, eval_durations_ns)
    ]

    return {
        "model": model,
        "n_concurrent": n_concurrent,
        "aggregate_wall_s": round(aggregate_wall, 2),
        "aggregate_eval_tokens": total_eval_tokens,
        "aggregate_tok_s": aggregate_tok_s,
        "per_worker_wall_s": [round(w, 2) for w in per_worker_wall],
        "per_worker_tok_s": per_worker_tok_s,
        "per_worker_eval_tokens": eval_counts,
        "errors": [e for e in errors if e],
        "gpu": sampler.summary(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    parser.add_argument("--phase", choices=["tok_s", "context", "parallel", "ctx_sweep", "all"], default="tok_s")
    parser.add_argument("--only", nargs="*", default=None, help="Restrict to these model tags")
    parser.add_argument("--tok-s-predict", type=int, default=500)
    parser.add_argument("--context-predict", type=int, default=80)
    parser.add_argument("--context-target-tokens", type=int, default=200_000)
    parser.add_argument("--parallel-predict", type=int, default=200)
    parser.add_argument("--parallel-counts", type=int, nargs="+", default=[1, 2, 4])
    parser.add_argument("--parallel-num-ctx", type=int, default=8192, help="num_ctx to use during parallel test")
    parser.add_argument("--ctx-sweep-values", type=int, nargs="+", default=[8192, 32768, 65536, 131072])
    parser.add_argument("--ctx-sweep-predict", type=int, default=100)
    parser.add_argument("--unload-between", action="store_true", help="Force unload between each test to measure cold loads")
    args = parser.parse_args()

    models = args.only or DEFAULT_MODELS
    out: Dict[str, Any] = {
        "ollama_url": OLLAMA_URL,
        "models": models,
        "phase": args.phase,
        "tok_s_tests": [],
        "context_tests": [],
        "parallel_tests": [],
        "ctx_sweep_tests": [],
    }

    try:
        if args.phase in ("tok_s", "all"):
            for m in models:
                for thinking_on in (True, False):
                    print(
                        f"[{time.strftime('%H:%M:%S')}] tok_s: {m} thinking={thinking_on}",
                        file=sys.stderr,
                        flush=True,
                    )
                    r = run_tok_s(m, thinking_on, args.tok_s_predict)
                    out["tok_s_tests"].append(r)
                    print(
                        f"  -> eval={r.get('tok_s_eval')} tok/s  prompt={r.get('tok_s_prompt')} tok/s  err={r.get('error')}",
                        file=sys.stderr,
                        flush=True,
                    )
                    if args.unload_between:
                        unload_model(m)

        if args.phase in ("ctx_sweep", "all"):
            for m in models:
                print(
                    f"[{time.strftime('%H:%M:%S')}] ctx_sweep: {m} values={args.ctx_sweep_values}",
                    file=sys.stderr,
                    flush=True,
                )
                rows = run_ctx_sweep(m, args.ctx_sweep_values, args.ctx_sweep_predict)
                out["ctx_sweep_tests"].extend(rows)
                for r in rows:
                    print(
                        f"  -> num_ctx={r['num_ctx']}  total_mem_mb={r.get('total_mem_mb')}  gpus={r.get('gpus_with_mem')}  tok_s={r.get('tok_s_eval')}",
                        file=sys.stderr,
                        flush=True,
                    )

        if args.phase in ("parallel", "all"):
            for m in models:
                for n in args.parallel_counts:
                    print(
                        f"[{time.strftime('%H:%M:%S')}] parallel: {m} n_concurrent={n}",
                        file=sys.stderr,
                        flush=True,
                    )
                    r = run_parallel_test(m, n, args.parallel_predict)
                    out["parallel_tests"].append(r)
                    print(
                        f"  -> aggregate={r.get('aggregate_tok_s')} tok/s  wall={r.get('aggregate_wall_s')}s  errors={len(r.get('errors', []))}",
                        file=sys.stderr,
                        flush=True,
                    )

        if args.phase in ("context", "all"):
            for m in models:
                print(
                    f"[{time.strftime('%H:%M:%S')}] context: {m} target={args.context_target_tokens}",
                    file=sys.stderr,
                    flush=True,
                )
                r = run_context_probe(m, args.context_predict, args.context_target_tokens)
                out["context_tests"].append(r)
                print(
                    f"  -> prompt_tokens={r.get('prompt_tokens')}  eval_tok_s={r.get('tok_s_eval')}  wall={r.get('wall_s')}s  err={r.get('error')}",
                    file=sys.stderr,
                    flush=True,
                )
                if args.unload_between:
                    unload_model(m)
    finally:
        json.dump(out, sys.stdout, indent=2)
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
