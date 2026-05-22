#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M"
echo
echo "=== v2 progress ==="
LATEST=$(ls -t ~/bench_out/run_260q_orcd_v2/*.json 2>/dev/null | head -1)
echo "file: $LATEST"
if [ -n "$LATEST" ]; then
  python3 - "$LATEST" <<'PY'
import json, sys, statistics
d = json.load(open(sys.argv[1]))
r = d["benchmarking_results"][0]["single_question_results"]
n = len(r)
errs = sum(1 for v in r.values() if v.get("error"))
hit_budget = sum(1 for v in r.values() if v.get("hit_budget"))
times = [v.get("time_elapsed", 0) for v in r.values()]
tools = [sum(1 for e in v.get("trace_events", []) if e.get("type") == "tool_call") for v in r.values()]
bulk_users = sum(1 for v in r.values() if any(
    e.get("tool_name") == "fetch_catalog_documents_bulk"
    for e in v.get("trace_events", []) if e.get("type") == "tool_call"))

print(f"completed: {n}/260")
print(f"errors:    {errs}  ({100*errs/n:.1f}%)" if n else "errors: 0")
print(f"hit budget cap: {hit_budget}")
print(f"bulk-fetch users: {bulk_users}  ({100*bulk_users/n:.0f}% of completed)" if n else "")
if times:
    ts = sorted(times)
    p = lambda q: ts[min(n-1, int(n*q))]
    print(f"wall time s:  mean={statistics.mean(times):6.1f}  p50={p(0.5):5.1f}  p90={p(0.9):5.1f}  p95={p(0.95):5.1f}  max={max(times):5.1f}")
if tools:
    ts = sorted(tools)
    p = lambda q: ts[min(n-1, int(n*q))]
    print(f"tool calls/q: mean={statistics.mean(tools):6.1f}  p50={p(0.5):5.0f}  p90={p(0.9):5.0f}  max={max(tools):5d}")
# error breakdown if any
if errs:
    from collections import Counter
    c = Counter()
    for v in r.values():
        err = v.get("error") or ""
        if err:
            c[err.split(":")[0][:60]] += 1
    print()
    print("error classes:")
    for k, n_ in c.most_common():
        print(f"  {n_:3d}  {k}")
PY
fi
echo
echo "=== latest log lines ==="
LATEST_LOG=$(ls -t ~/archi-bench-v2.*.out 2>/dev/null | head -1)
tail -8 "$LATEST_LOG"
REMOTE
