#!/usr/bin/env python3
"""
Create a synthetic benchmark result file from GPT-5 production reference answers.

Reads curated_questions_categorized.json and produces a result file in the same
format as other bench_out/results/*.json files, with the reference_answer as the
generated answer. This lets us run the same judge pipeline on production answers
to establish a ceiling/baseline.

Usage:
    python scripts/create_reference_baseline.py
    python scripts/create_reference_baseline.py --questions configs/submit76/curated_questions_categorized.json --output bench_out/results/gpt5-production-reference.json
"""

import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="Create GPT-5 reference baseline result file")
    parser.add_argument("--questions", type=str,
                        default="configs/submit76/curated_questions_categorized.json",
                        help="Path to curated questions with reference_answer fields")
    parser.add_argument("--output", type=str,
                        default="bench_out/results/gpt5-production-reference.json",
                        help="Output path for synthetic result file")
    args = parser.parse_args()

    with open(args.questions) as f:
        questions = json.load(f)

    single_question_results = {}
    included = 0
    skipped = 0

    for i, q in enumerate(questions):
        qkey = f"question_{i + 1}"
        ref = q.get("reference_answer", "")

        if not ref or ref.strip() in ("N/A", "", "none"):
            skipped += 1
            continue

        included += 1
        single_question_results[qkey] = {
            "time_elapsed": 0.0,
            "question": q["question"],
            "reference_answer": "N/A",  # no external reference for the reference itself
            "answer": ref,
            "messages": [],
            "reference_sources_match_fields": [],
            "reference_sources_metadata": [],
            "sources_metadata": [],
            "sources_trunc_content": [],
            # Preserve metadata for post-hoc analysis
            "category": q.get("category", ""),
            "answerable_from_docs": q.get("answerable_from_docs", True),
            "time_sensitive": q.get("time_sensitive", False),
        }

    data = {
        "benchmarking_results": [
            {
                "single_question_results": single_question_results,
                "total_results": {
                    "num_questions": included,
                    "description": "GPT-5 production reference answers treated as benchmark output"
                },
                "configuration_file": "gpt5-production-reference (synthetic)",
                "configuration": {
                    "name": "gpt5-production-reference",
                    "description": "Production GPT-5 + CMSCompOpsAgent answers from conversations_310326.csv. "
                                   "No sources/tool-calls included — just the final answer text.",
                    "agent_class": "CMSCompOpsAgent",
                    "model": "gpt-5 (production, via OpenAI API)",
                    "tools": "full production toolset (Jira, OpenSearch/MONIT, docs)",
                }
            }
        ],
        "metadata": {
            "source": "curated_questions_categorized.json reference_answer field",
            "total_questions_in_source": len(questions),
            "questions_with_reference": included,
            "questions_without_reference": skipped,
        }
    }

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Created {args.output}")
    print(f"  {included} questions with reference answers included")
    print(f"  {skipped} questions without reference answers skipped")


if __name__ == "__main__":
    main()
