#!/usr/bin/env python3
"""Estimate input tokens for GPT-4.1 judge calls across all result files."""
import json, os, tiktoken

enc = tiktoken.encoding_for_model("gpt-4o")  # gpt-4.1 uses same tokenizer

def tok(text):
    return len(enc.encode(str(text))) if text else 0

# Fixed text that appears in every call
RUBRIC = (
    "You are an expert evaluator for a CMS Computing Operations AI assistant. "
    "Evaluate the generated answer on the following dimensions using a 1-5 scale.\n\n"
    "IMPORTANT PRINCIPLES:\n"
    "- This is a reference-free evaluation. Score based on the answer's own quality.\n"
    "- Unsupported specific claims (invented ticket numbers, dates, data values with "
    "no cited source) are WORSE than honest vagueness.\n"
    "- Non-responses score 1 on all dimensions.\n"
    "- Length is not a proxy for quality. A concise, precise answer is better than "
    "a verbose answer padded with generic information.\n\n"
    "**Relevance** - Does the answer address the specific question?\n"
    "5: Directly and precisely addresses the question\n"
    "4: Addresses with minor tangential content\n"
    "3: Partially addresses, significant off-topic material\n"
    "2: Mostly off-topic\n"
    "1: Completely irrelevant or non-response\n\n"
    "**Completeness** - How many aspects does the answer address?\n"
    "5: All aspects, no significant gaps\n"
    "4: Most aspects, one minor gap\n"
    "3: Core question but misses important context\n"
    "2: Only partially addresses\n"
    "1: Does not meaningfully address\n\n"
    "**Specificity** - Concrete, actionable details or vague generalities?\n"
    "CRITICAL: unsupported specifics score LOWER than honest vagueness.\n"
    "5: Rich in concrete, well-supported details\n"
    "4: Useful specific details, mostly supported\n"
    "3: Mix of specific and vague\n"
    "2: Mostly vague/generic, OR unsupported specifics\n"
    "1: Entirely vague, refusal, or non-response\n\n"
    "**Helpfulness** - Could a CMS operator make progress?\n"
    "5: Could act immediately\n"
    "4: Useful, path forward\n"
    "3: Starting point, needs investigation\n"
    "2: Minimally useful\n"
    "1: Not useful or harmful\n\n"
    "**Source Faithfulness** (only when sources provided) - Does the answer reflect its sources?\n"
    "5: All claims supported\n"
    "4: Most supported, minor extrapolations\n"
    "3: Mix supported/unsupported\n"
    "2: Significant misrepresentation\n"
    "1: Contradicts sources\n\n"
    "Evaluate each dimension BEFORE assigning scores. Think step-by-step.\n"
    "Return JSON with \"reasoning\" and integer scores 1-5."
)

SYSTEM_MSG = "You are an expert evaluator. Always respond with valid JSON."
FMT_LABELS = "Question:\n\nRetrieved Sources:\n\nGenerated Answer:\n\n"

fixed = tok(RUBRIC) + tok(SYSTEM_MSG) + tok(FMT_LABELS)
print(f"Fixed overhead per call: {fixed} tokens (rubric={tok(RUBRIC)}, sys={tok(SYSTEM_MSG)}, fmt={tok(FMT_LABELS)})")
print()

results_dir = "bench_out/results"
print(f"{'Config':<35} {'Calls':>5} {'Q':>7} {'Ans':>8} {'Src':>8} {'Fixed':>8} {'TOTAL':>10} {'Avg':>6}")
print("-" * 93)

grand_total = 0
grand_calls = 0

for fname in sorted(os.listdir(results_dir)):
    if not fname.endswith(".json"):
        continue
    with open(os.path.join(results_dir, fname)) as f:
        d = json.load(f)
    sqr = d["benchmarking_results"][0]["single_question_results"]

    calls = 0
    q_sum = a_sum = s_sum = 0

    for v in sqr.values():
        ans = v.get("answer", "")
        if not ans:
            continue
        calls += 1
        q_sum += tok(v["question"])
        a_sum += tok(ans)
        s_sum += tok(v.get("sources_trunc_content", ""))

    total = q_sum + a_sum + s_sum + fixed * calls
    avg = total // calls if calls else 0
    label = fname.replace(".json", "")
    print(f"{label:<35} {calls:>5} {q_sum:>7} {a_sum:>8} {s_sum:>8} {fixed*calls:>8} {total:>10} {avg:>6}")
    grand_total += total
    grand_calls += calls

print("-" * 93)
print(f"{'TOTAL':<35} {grand_calls:>5} {'':>7} {'':>8} {'':>8} {'':>8} {grand_total:>10} {grand_total//grand_calls:>6}")

print(f"\n{'='*60}")
print(f"INPUT:   {grand_total:>10,} tokens x $2.00/M = ${grand_total * 2 / 1e6:>7.2f}")
output_tok = grand_calls * 200
print(f"OUTPUT:  {output_tok:>10,} tokens x $8.00/M = ${output_tok * 8 / 1e6:>7.2f}")
single = grand_total * 2 / 1e6 + output_tok * 8 / 1e6
print(f"SINGLE RUN TOTAL:                     ${single:>7.2f}")
print(f"DOUBLE RUN (reliability):             ${2*single:>7.2f}")
print(f"{'='*60}")
