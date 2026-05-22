"""Analyze trace_events from the live bench results to find the real bottleneck.

Pulls the freshest results JSON, walks single_question_results, and answers:
  1. Per-question: how many tool calls, how many LLM rounds, total time
  2. Per-tool: count, mean/p50/p95 latency
  3. Time decomposition: LLM time vs tool time vs idle/overhead
  4. Question-time distribution split by error vs success
"""
import json, sys, os, statistics, glob
from collections import defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else None
if not path:
    cands = sorted(glob.glob(os.path.expanduser("~/bench_out/run_260q_orcd/*.json")), key=os.path.getmtime)
    if not cands:
        print("no results file found")
        sys.exit(1)
    path = cands[-1]

print(f"reading {path}")
with open(path) as fh:
    d = json.load(fh)

runs = d.get("benchmarking_results", [])
if not runs:
    print("no benchmarking_results")
    sys.exit(1)
r = runs[0].get("single_question_results", {})
print(f"questions completed: {len(r)}")

# scan one record to learn event shape
sample = next(iter(r.values()), None) if r else None
if sample is None:
    sys.exit(0)
print(f"keys per question: {sorted(sample.keys())}")
evs = sample.get("trace_events", [])
print(f"event count in sample q: {len(evs)}")
if evs:
    print(f"first event keys: {sorted(evs[0].keys())}")
    print(f"event types observed (top 5):")
    types = defaultdict(int)
    for q in r.values():
        for ev in q.get("trace_events", []):
            types[ev.get("type", "?")] += 1
    for t, c in sorted(types.items(), key=lambda kv: -kv[1])[:10]:
        print(f"  {t:30s} {c}")
print()

# per-question stats
n_tools_list = []
n_llm_list   = []
time_list    = []
err_list     = []
for qid, q in r.items():
    evs = q.get("trace_events", [])
    n_t = sum(1 for e in evs if e.get("type") == "tool_call")
    # LLM rounds: count "ai_message" or "llm_call" or chunk types
    n_llm = sum(1 for e in evs if e.get("type") in ("ai_message", "llm_call", "llm_chunk_start"))
    n_tools_list.append(n_t)
    n_llm_list.append(n_llm)
    time_list.append(q.get("time_elapsed", 0))
    err_list.append(bool(q.get("error")))

def stats(name, xs):
    if not xs: return
    xs_s = sorted(xs)
    n = len(xs_s)
    p = lambda q: xs_s[min(n - 1, int(n * q))]
    print(f"  {name:18s}  n={n}  mean={statistics.mean(xs):7.2f}  p50={p(0.5):6.1f}  p90={p(0.9):6.1f}  p95={p(0.95):6.1f}  max={max(xs):7.1f}")

print("PER-QUESTION DISTRIBUTIONS")
stats("time_elapsed (s)", time_list)
stats("tool_calls / q",  n_tools_list)
stats("llm rounds / q",  n_llm_list)
print(f"  errors: {sum(err_list)} / {len(err_list)}  ({100*sum(err_list)/max(len(err_list),1):.1f}%)")
print()

# per-tool latency: look for tool_call events that include a start/end ts or duration
print("PER-TOOL LATENCY")
tool_calls_by_name = defaultdict(list)
for q in r.values():
    for ev in q.get("trace_events", []):
        if ev.get("type") != "tool_call":
            continue
        # what field holds the name?
        name = ev.get("name") or ev.get("tool") or ev.get("tool_name") or "?"
        # latency
        dur = ev.get("duration") or ev.get("duration_s") or ev.get("elapsed")
        if dur is None and "start_ts" in ev and "end_ts" in ev:
            dur = ev["end_ts"] - ev["start_ts"]
        if dur is None and "start" in ev and "end" in ev:
            try:
                dur = ev["end"] - ev["start"]
            except Exception:
                dur = None
        if dur is not None:
            tool_calls_by_name[name].append(float(dur))
        else:
            tool_calls_by_name[name].append(None)

if not any(any(v is not None for v in lats) for lats in tool_calls_by_name.values()):
    print("  no duration fields on tool_call events — dumping a raw sample so we know shape:")
    for q in r.values():
        for ev in q.get("trace_events", []):
            if ev.get("type") == "tool_call":
                print("  raw tool_call event:", json.dumps(ev, default=str)[:500])
                break
        else:
            continue
        break

for name, lats in sorted(tool_calls_by_name.items(), key=lambda kv: -len(kv[1])):
    clean = [x for x in lats if x is not None]
    if clean:
        s = sorted(clean)
        n = len(s)
        p = lambda q: s[min(n - 1, int(n * q))]
        print(f"  {name:32s}  n={len(lats):4d}  mean={statistics.mean(clean):6.2f}s  p50={p(0.5):5.2f}s  p95={p(0.95):5.2f}s  max={max(clean):6.2f}s")
    else:
        print(f"  {name:32s}  n={len(lats):4d}  (no durations available)")
print()

# Time-decomposition per question if events have timestamps
print("WALL-TIME DECOMPOSITION (avg over questions, when timestamps available)")
agg_tool_t = []
agg_llm_t  = []
agg_total  = []
for q in r.values():
    evs = q.get("trace_events", [])
    if not evs:
        continue
    total = q.get("time_elapsed", 0)
    # Try to extract per-event duration via paired start/end if "ts" present
    tool_t = 0.0
    llm_t  = 0.0
    has_durations = False
    for ev in evs:
        d = ev.get("duration") or ev.get("duration_s") or ev.get("elapsed")
        if d is None and "start_ts" in ev and "end_ts" in ev:
            d = ev["end_ts"] - ev["start_ts"]
        if d is None and "start" in ev and "end" in ev:
            try: d = ev["end"] - ev["start"]
            except Exception: d = None
        if d is None:
            continue
        has_durations = True
        if ev.get("type") == "tool_call":
            tool_t += float(d)
        elif ev.get("type") in ("ai_message", "llm_call", "llm_chunk"):
            llm_t += float(d)
    if has_durations:
        agg_tool_t.append(tool_t)
        agg_llm_t.append(llm_t)
        agg_total.append(total)

if agg_total:
    mean_total = statistics.mean(agg_total)
    mean_tool  = statistics.mean(agg_tool_t)
    mean_llm   = statistics.mean(agg_llm_t)
    print(f"  mean total: {mean_total:.2f}s")
    print(f"  mean tool : {mean_tool:.2f}s  ({100*mean_tool/max(mean_total,1e-6):.0f}%)")
    print(f"  mean llm  : {mean_llm:.2f}s  ({100*mean_llm/max(mean_total,1e-6):.0f}%)")
    print(f"  mean other: {mean_total - mean_tool - mean_llm:.2f}s  (gap = serialization/wait/queueing)")
else:
    print("  trace_events lack per-event durations — can't decompose; we'll need to add timing in the bench")

# Histogram of tool-call counts
print()
print("TOOL-CALL COUNT HISTOGRAM")
buckets = defaultdict(int)
for n in n_tools_list:
    if n == 0:    b = "0"
    elif n <= 2:  b = "1-2"
    elif n <= 5:  b = "3-5"
    elif n <= 10: b = "6-10"
    elif n <= 20: b = "11-20"
    else:         b = "21+"
    buckets[b] += 1
for b in ("0","1-2","3-5","6-10","11-20","21+"):
    print(f"  {b:6s} {buckets[b]:4d}  {'#' * buckets[b]}")
