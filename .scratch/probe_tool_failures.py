"""Look at tool_output.output_preview for failure indicators.

Detection heuristics for failure:
  - starts with "Error" / "ToolException" / "Traceback"
  - contains "denied" / "forbidden" / "401" / "403" / "500"
  - contains "not found" / "No matching" / "0 documents" / "Total matching documents: 0"
  - empty output

Also: time-to-resolve via consecutive same-tool retries.
"""
import json, os, glob, re
from collections import defaultdict, Counter

cands = sorted(glob.glob(os.path.expanduser("~/bench_out/run_260q_orcd/*.json")), key=os.path.getmtime)
path = cands[-1]
print("file:", path)
d = json.load(open(path))
r = d["benchmarking_results"][0]["single_question_results"]
print("n questions:", len(r))

FAIL_PATTERNS = [
    ("explicit_error",   re.compile(r"^\s*(Error|ToolException|Traceback|Exception)[\s:]", re.IGNORECASE)),
    ("denied",           re.compile(r"\b(denied|forbidden|unauthorized|access\s+to.+denied)\b", re.IGNORECASE)),
    ("http_error",       re.compile(r"\b(HTTP\s+)?(40[0-4]|50[0-3])\b")),
    ("empty_result",     re.compile(r"Total matching documents:\s*0|^\s*\[\]\s*$|^\s*\{\}\s*$|No matching|No results found|0 hits|^\s*$")),
    ("schema_error",     re.compile(r"unknown field|invalid query|parse_exception|illegal_argument", re.IGNORECASE)),
    ("timeout",          re.compile(r"timeout|timed\s+out", re.IGNORECASE)),
]

per_tool_failures = defaultdict(lambda: defaultdict(int))   # tool -> failure_type -> count
per_tool_total = Counter()
empty_outputs_by_tool = Counter()
nonempty_outputs_by_tool = Counter()

for q in r.values():
    for ev in q.get("trace_events", []):
        if ev.get("type") != "tool_output": continue
        name = ev.get("tool_name", "?")
        out = ev.get("output_preview", "") or ""
        per_tool_total[name] += 1
        matched_any = False
        for label, pat in FAIL_PATTERNS:
            if pat.search(out):
                per_tool_failures[name][label] += 1
                matched_any = True
        if out.strip():
            nonempty_outputs_by_tool[name] += 1
        else:
            empty_outputs_by_tool[name] += 1

print()
print("PER-TOOL FAILURE BREAKDOWN")
print(f"  {'tool':32s} {'total':>6s}  failures by category")
for tool, total in per_tool_total.most_common():
    cats = per_tool_failures.get(tool, {})
    if not cats: continue
    cat_str = "  ".join(f"{k}={v}({100*v/total:.0f}%)" for k, v in sorted(cats.items(), key=lambda kv: -kv[1]))
    print(f"  {tool:32s} {total:6d}  {cat_str}")

print()
print("PER-TOOL OVERALL FAILURE RATE (any category)")
print(f"  {'tool':32s} {'total':>6s}  {'any_fail':>9s}  {'%':>6s}")
for tool, total in per_tool_total.most_common():
    any_fail = 0
    for q in r.values():
        for ev in q.get("trace_events", []):
            if ev.get("type") != "tool_output" or ev.get("tool_name") != tool:
                continue
            out = ev.get("output_preview", "") or ""
            for label, pat in FAIL_PATTERNS:
                if pat.search(out):
                    any_fail += 1
                    break
    print(f"  {tool:32s} {total:6d}  {any_fail:9d}  {100*any_fail/max(total,1):5.1f}%")

print()
print("EMPTY-RESULT RATE BY TOOL")
print(f"  {'tool':32s} {'total':>6s}  {'empty':>6s}  {'%':>6s}")
for tool, total in per_tool_total.most_common():
    e = empty_outputs_by_tool.get(tool, 0)
    print(f"  {tool:32s} {total:6d}  {e:6d}  {100*e/max(total,1):5.1f}%")

print()
print("AGENT RETRY BEHAVIOR — same tool back-to-back per question")
qs_with_retry = 0
total_retries = 0
retry_after_fail = 0   # retry where prior tool_output was a failure
retry_after_ok = 0
for q in r.values():
    evs = q.get("trace_events", [])
    prev_tool = None
    prev_fail = False
    any_retry = False
    for ev in evs:
        if ev.get("type") == "tool_call":
            cur = ev.get("tool_name")
            if cur == prev_tool:
                total_retries += 1
                any_retry = True
                if prev_fail:
                    retry_after_fail += 1
                else:
                    retry_after_ok += 1
            prev_tool = cur
        elif ev.get("type") == "tool_output":
            out = ev.get("output_preview", "") or ""
            prev_fail = any(p.search(out) for _, p in FAIL_PATTERNS)
    if any_retry:
        qs_with_retry += 1
print(f"  questions with at least one same-tool retry: {qs_with_retry}/{len(r)}  ({100*qs_with_retry/len(r):.0f}%)")
print(f"  total retries:                {total_retries}")
print(f"  retry after a failure output: {retry_after_fail}  ({100*retry_after_fail/max(total_retries,1):.0f}%)")
print(f"  retry after an ok output:     {retry_after_ok}   ({100*retry_after_ok/max(total_retries,1):.0f}%)")

print()
print("WORST QUESTIONS BY TOOL-CALL COUNT")
ranked = sorted(r.items(), key=lambda kv: -sum(1 for e in kv[1].get("trace_events", []) if e.get("type") == "tool_call"))
print(f"  {'qid':18s} {'tools':>6s} {'time':>7s} {'err?':>5s}  question")
for qid, q in ranked[:10]:
    n_t = sum(1 for e in q.get("trace_events", []) if e.get("type") == "tool_call")
    t = q.get("time_elapsed", 0)
    qstr = (q.get("question") or "")[:80].replace("\n", " ")
    err = "Y" if q.get("error") else "."
    print(f"  {qid:18s} {n_t:6d} {t:7.1f}  {err:>4s}  {qstr}")
