#!/usr/bin/env python3
"""Prepare judge batch files from eval results for subagent evaluation.

Reads the raw evaluation results and creates per-config batch files
with all fields needed for the reference-free rubric (v4):
  - question, answer, qkey
  - answerable_from_docs (metadata for post-hoc analysis, NOT scoring)
  - sources_trunc_content (for source_faithfulness dimension)

Also generates a separate 'compops-gpt5' config that treats the
production GPT-5 reference answers as the answers to be judged.

Also splits each config into N batches for parallel subagent judging.

Usage:
    python scripts/prepare_judge_batches.py
    python scripts/prepare_judge_batches.py --batches-per-config 4
    python scripts/prepare_judge_batches.py --max-source-chars 5000
"""

import argparse
import json
import math
import os

INPUT_FILE = "bench_out/eval-curated-retried.json"
REFERENCE_FILE = "configs/submit76/curated_questions_categorized.json"
OUTPUT_DIR = "bench_out/judge_batches_v4"
SPLITS_DIR = os.path.join(OUTPUT_DIR, "splits")

# Truncate individual source entries longer than this
DEFAULT_MAX_SOURCE_CHARS = 5000


def truncate_sources(sources_raw, max_chars):
    """Truncate source content to fit in batch files."""
    if not sources_raw:
        return ""
    if isinstance(sources_raw, str):
        # It's a stringified list — parse it
        try:
            sources_raw = eval(sources_raw)
        except Exception:
            return sources_raw[:max_chars] if len(sources_raw) > max_chars else sources_raw

    if isinstance(sources_raw, list):
        truncated = []
        for s in sources_raw:
            s_str = str(s)
            if len(s_str) > max_chars:
                s_str = s_str[:max_chars] + "... [truncated]"
            truncated.append(s_str)
        return "\n---\n".join(truncated)

    return str(sources_raw)[:max_chars]


def main():
    parser = argparse.ArgumentParser(description="Prepare judge batch files")
    parser.add_argument("--batches-per-config", type=int, default=4,
                        help="Number of batches to split each config into (default: 4)")
    parser.add_argument("--max-source-chars", type=int, default=DEFAULT_MAX_SOURCE_CHARS,
                        help="Max chars per source entry (default: 5000)")
    parser.add_argument("--input", type=str, default=INPUT_FILE,
                        help="Input eval results file")
    parser.add_argument("--references", type=str, default=REFERENCE_FILE,
                        help="Curated questions file with reference answers")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(SPLITS_DIR, exist_ok=True)

    # Load reference answers
    ref_map = {}
    if os.path.exists(args.references):
        print(f"Loading references from {args.references}...")
        with open(args.references) as f:
            ref_data = json.load(f)
        for item in ref_data:
            q = item["question"].strip()
            ref_map[q] = item.get("reference_answer", "N/A")
        has_ref = sum(1 for v in ref_map.values() if v and v not in ("N/A", "", "None"))
        print(f"  {has_ref}/{len(ref_map)} questions have reference answers\n")
    else:
        print(f"Warning: reference file {args.references} not found, proceeding without references\n")

    print(f"Loading {args.input}...")
    with open(args.input) as f:
        data = json.load(f)

    configs = data["benchmarking_results"]
    print(f"Found {len(configs)} configs\n")

    for cfg_idx, cfg in enumerate(configs):
        sqr = cfg["single_question_results"]
        config_file = cfg["configuration_file"]
        name = config_file.split("/")[-1].replace(".yaml", "")

        # Detect if this config has sources
        has_sources = any(v.get("sources_trunc_content") for v in sqr.values())

        # Build question list
        questions = []
        for qkey, qdata in sqr.items():
            entry = {
                "qkey": qkey,
                "question": qdata["question"],
                "answer": qdata.get("answer", ""),
                "answerable_from_docs": qdata.get("answerable_from_docs", True),
            }
            # Include sources for source_faithfulness scoring
            if has_sources:
                raw_src = qdata.get("sources_trunc_content", "")
                entry["sources_trunc_content"] = truncate_sources(raw_src, args.max_source_chars)

            questions.append(entry)

        # Count stats
        n_with_answer = sum(1 for q in questions if q["answer"])
        n_with_sources = sum(1 for q in questions if q.get("sources_trunc_content"))

        print(f"Config {cfg_idx}: {name}")
        print(f"  questions: {len(questions)}, with_answer: {n_with_answer}, "
              f"has_sources: {has_sources} ({n_with_sources} with content)")

        # Save full config batch
        batch_data = {
            "config_name": name,
            "config_index": cfg_idx,
            "has_sources": has_sources,
            "num_questions": len(questions),
            "questions": questions,
        }
        batch_path = os.path.join(OUTPUT_DIR, f"{name}.json")
        with open(batch_path, "w") as f:
            json.dump(batch_data, f, indent=2)

        # Split into sub-batches
        n_batches = args.batches_per_config
        batch_size = math.ceil(len(questions) / n_batches)

        for b in range(n_batches):
            start = b * batch_size
            end = min(start + batch_size, len(questions))
            if start >= len(questions):
                break

            split_data = {
                "config_name": name,
                "config_index": cfg_idx,
                "has_sources": has_sources,
                "batch_num": b,
                "batch_start": start,
                "num_questions": end - start,
                "questions": questions[start:end],
            }
            split_path = os.path.join(SPLITS_DIR, f"{name}_batch{b:02d}.json")
            with open(split_path, "w") as f:
                json.dump(split_data, f, indent=2)

        print(f"  -> {n_batches} batches of ~{batch_size} questions")
        print()

    # --- Generate compops-gpt5 synthetic config ---
    # Uses the production GPT-5 reference answers as the "answer" to judge.
    # These come from the curated questions file, not from any eval run.
    if ref_map:
        gpt5_name = "compops-gpt5"
        gpt5_questions = []
        # Use qkeys from the first config as canonical ordering
        first_cfg = configs[0]
        first_sqr = first_cfg["single_question_results"]
        for qkey, qdata in first_sqr.items():
            q_text = qdata["question"].strip()
            ref_ans = ref_map.get(q_text, "")
            if not ref_ans or ref_ans == "N/A":
                continue
            gpt5_questions.append({
                "qkey": qkey,
                "question": qdata["question"],
                "answer": ref_ans,
                "answerable_from_docs": qdata.get("answerable_from_docs", True),
            })

        # GPT-5 answers contain inline source citations (URLs, Jira refs)
        n_with_sources = sum(1 for q in gpt5_questions if "http" in q["answer"] or "Sources" in q["answer"])
        print(f"Config (synthetic): {gpt5_name}")
        print(f"  questions: {len(gpt5_questions)} (from reference answers, {n_with_sources} with inline sources)")

        gpt5_batch = {
            "config_name": gpt5_name,
            "config_index": len(configs),
            "has_sources": True,
            "num_questions": len(gpt5_questions),
            "questions": gpt5_questions,
        }
        gpt5_path = os.path.join(OUTPUT_DIR, f"{gpt5_name}.json")
        with open(gpt5_path, "w") as f:
            json.dump(gpt5_batch, f, indent=2)

        # Split into sub-batches
        n_batches = args.batches_per_config
        batch_size = math.ceil(len(gpt5_questions) / n_batches)
        for b in range(n_batches):
            start = b * batch_size
            end = min(start + batch_size, len(gpt5_questions))
            if start >= len(gpt5_questions):
                break
            split_data = {
                "config_name": gpt5_name,
                "config_index": len(configs),
                "has_sources": True,
                "batch_num": b,
                "batch_start": start,
                "num_questions": end - start,
                "questions": gpt5_questions[start:end],
            }
            split_path = os.path.join(SPLITS_DIR, f"{gpt5_name}_batch{b:02d}.json")
            with open(split_path, "w") as f:
                json.dump(split_data, f, indent=2)

        print(f"  -> {n_batches} batches of ~{batch_size} questions")
        print()

    # Summary of batch sizes
    total_size = 0
    for fname in os.listdir(OUTPUT_DIR):
        if fname.endswith(".json"):
            fpath = os.path.join(OUTPUT_DIR, fname)
            total_size += os.path.getsize(fpath)
    for fname in os.listdir(SPLITS_DIR):
        fpath = os.path.join(SPLITS_DIR, fname)
        total_size += os.path.getsize(fpath)
    print(f"Total batch data: {total_size / 1e6:.1f} MB")
    print(f"Output: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()
