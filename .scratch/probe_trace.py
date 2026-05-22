"""Inspect what's actually in trace_events. We need to know:
  - all event types
  - what fields each type carries
  - is there a tool_result / error / output event?
  - what does an error-flagged question look like?
"""
import json, sys, os, glob
from collections import defaultdict, Counter

cands = sorted(glob.glob(os.path.expanduser("~/bench_out/run_260q_orcd/*.json")), key=os.path.getmtime)
path = cands[-1]
print("file:", path)
d = json.load(open(path))
r = d["benchmarking_results"][0]["single_question_results"]
print("n questions:", len(r))

type_counts = Counter()
fields_by_type = defaultdict(set)
samples_by_type = {}
for q in r.values():
    for ev in q.get("trace_events", []):
        t = ev.get("type", "?")
        type_counts[t] += 1
        for k in ev.keys():
            fields_by_type[t].add(k)
        if t not in samples_by_type:
            samples_by_type[t] = ev

print()
print("event types in trace_events:")
for t, c in type_counts.most_common():
    print(f"  {t:24s}  {c:6d}  fields={sorted(fields_by_type[t])}")

print()
print("sample of each event type:")
for t, ev in samples_by_type.items():
    print(f"  ---- {t} ----")
    print("   ", json.dumps(ev, default=str)[:600])

print()
print("HOW MANY tool_call events have observable failure/error indicator?")
# Even if there's no tool_result event, the next event(s) after a tool_call may indicate retry of same tool
# Check: do tool_call events come back-to-back with same name and similar args? That's a retry, which suggests failure.
retry_count = 0
total_tool = 0
back_to_back_same_tool = 0
for q in r.values():
    evs = [e for e in q.get("trace_events", []) if e.get("type") == "tool_call"]
    total_tool += len(evs)
    for i in range(1, len(evs)):
        if evs[i].get("tool_name") == evs[i-1].get("tool_name"):
            back_to_back_same_tool += 1
print(f"  total tool_call events: {total_tool}")
print(f"  consecutive same-tool calls: {back_to_back_same_tool}  ({100*back_to_back_same_tool/max(total_tool,1):.1f}%)")

print()
print("ERROR ANALYSIS")
err_qs = [(qid, q) for qid, q in r.items() if q.get("error")]
no_tool_err = sum(1 for _, q in err_qs if not [e for e in q.get("trace_events", []) if e.get("type") == "tool_call"])
print(f"  total error questions: {len(err_qs)}")
print(f"  errors that made 0 tool calls: {no_tool_err}")
print(f"  errors that made >=1 tool calls: {len(err_qs) - no_tool_err}")
print()
print("  sample error messages (first 5):")
seen = set()
for qid, q in err_qs[:30]:
    err = (q.get("error") or "")[:200]
    key = err.split(":")[0]
    if key in seen:
        continue
    seen.add(key)
    print(f"    [{qid}] tools={sum(1 for e in q.get('trace_events',[]) if e.get('type')=='tool_call')}  err={err}")
    if len(seen) >= 6:
        break

print()
print("ARGS BIGGEST OFFENDERS (longest argument strings = potential malformed/huge queries)")
big_args = []
for q in r.values():
    for ev in q.get("trace_events", []):
        if ev.get("type") != "tool_call": continue
        a = json.dumps(ev.get("args", {}), default=str)
        big_args.append((len(a), ev.get("tool_name", "?"), a))
big_args.sort(reverse=True)
for size, name, a in big_args[:5]:
    print(f"  {name:32s}  arg_len={size}")
    print(f"    {a[:200]}")

print()
print("AGENT BEHAVIOR — fraction of questions where same (tool,args) repeats")
same_invocations = 0
total_invocations = 0
for q in r.values():
    evs = [e for e in q.get("trace_events", []) if e.get("type") == "tool_call"]
    seen = set()
    for ev in evs:
        key = (ev.get("tool_name"), json.dumps(ev.get("args", {}), sort_keys=True, default=str))
        if key in seen:
            same_invocations += 1
        seen.add(key)
        total_invocations += 1
print(f"  total tool invocations: {total_invocations}")
print(f"  exact-duplicate invocations within same question: {same_invocations}  ({100*same_invocations/max(total_invocations,1):.1f}%)")
