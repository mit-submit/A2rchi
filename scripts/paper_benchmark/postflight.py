#!/usr/bin/env python3
"""Postflight validation for corrected ORCD/vLLM benchmark result files."""

import argparse
import json
import sys
from pathlib import Path


def load_results(path: Path):
    data = json.loads(path.read_text())
    results = data.get("benchmarking_results", [{}])[0].get("single_question_results", {})
    return data, results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_json")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--expected-tier", default="orcd-vllm-corrected")
    parser.add_argument("--require-sources", action="store_true")
    parser.add_argument("--max-errors", type=int, default=0)
    parser.add_argument("--max-budget-hits", type=int)
    parser.add_argument("--manifest-out")
    args = parser.parse_args()

    path = Path(args.result_json)
    data, results = load_results(path)
    manifest = data.get("run_manifest") or {}
    failures = []
    if manifest.get("tier") != args.expected_tier:
        failures.append(f"tier={manifest.get('tier')!r}, expected {args.expected_tier!r}")
    if args.expected_count is not None and len(results) != args.expected_count:
        failures.append(f"result count={len(results)}, expected {args.expected_count}")
    serving = manifest.get("model_serving") or {}
    if manifest.get("model_backend") == "vllm":
        required_serving = ("id", "backend", "dtype", "quantization", "fp8", "tensor_parallel", "thinking_enabled")
        missing_serving = [key for key in required_serving if serving.get(key) in (None, "")]
        if missing_serving:
            failures.append(f"model_serving missing {missing_serving}")

    errors = {qid: r.get("error") for qid, r in results.items() if r.get("error")}
    budget_hits = [qid for qid, r in results.items() if r.get("hit_budget")]
    missing_model = [qid for qid, r in results.items() if not r.get("model_used") or r.get("model_used") == "?"]
    missing_traces = [qid for qid, r in results.items() if not r.get("trace_events")]
    missing_answer = [qid for qid, r in results.items() if not (r.get("answer") or "").strip() and not r.get("error")]
    missing_sources = [
        qid for qid, r in results.items()
        if not r.get("sources_metadata") and not any(e.get("type") in {"tool_call", "rag_retrieve"} for e in r.get("trace_events", []))
    ]

    if missing_model:
        failures.append(f"{len(missing_model)} rows missing model_used")
    if missing_traces:
        failures.append(f"{len(missing_traces)} rows missing trace_events")
    if missing_answer:
        failures.append(f"{len(missing_answer)} completed rows missing answer text")
    if args.require_sources and missing_sources:
        failures.append(f"{len(missing_sources)} rows missing source/evidence metadata")
    if len(errors) > args.max_errors:
        failures.append(f"errors={len(errors)} exceeds max_errors={args.max_errors}")
    if args.max_budget_hits is not None and len(budget_hits) > args.max_budget_hits:
        failures.append(f"budget_hits={len(budget_hits)} exceeds max_budget_hits={args.max_budget_hits}")

    report = {
        "result_json": str(path),
        "tier": manifest.get("tier"),
        "tool_set": manifest.get("tool_set"),
        "count": len(results),
        "errors": len(errors),
        "error_qids": list(errors)[:25],
        "budget_hits": len(budget_hits),
        "budget_qids": budget_hits[:25],
        "missing_model": len(missing_model),
        "missing_traces": len(missing_traces),
        "missing_answer": len(missing_answer),
        "missing_sources_or_evidence": len(missing_sources),
        "failures": failures,
    }
    if args.manifest_out:
        Path(args.manifest_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.manifest_out).write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
