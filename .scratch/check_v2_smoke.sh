#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M"
echo "=== latest v2 result file ==="
LATEST=$(ls -t ~/bench_out/run_260q_orcd_v2/*.json 2>/dev/null | head -1)
echo "$LATEST"
if [ -n "$LATEST" ]; then
  python3 - "$LATEST" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
r = d["benchmarking_results"][0]["single_question_results"]
print(f"questions in file: {len(r)}")
for qid, q in r.items():
    evs = q.get("trace_events", [])
    n_calls = sum(1 for e in evs if e.get("type") == "tool_call")
    n_outs  = sum(1 for e in evs if e.get("type") == "tool_output")
    n_dur   = sum(1 for e in evs if e.get("type") == "tool_output" and "duration_s" in e)
    durs    = [e["duration_s"] for e in evs if e.get("type") == "tool_output" and "duration_s" in e]
    err     = q.get("error")
    hit_budget = q.get("hit_budget")
    print(f"  {qid}: tools={n_calls} outputs={n_outs} with_duration={n_dur} "
          f"time={q.get('time_elapsed', 0):.1f}s err={bool(err)} hit_budget={hit_budget}")
    if durs:
        durs_s = sorted(durs)
        print(f"    tool duration_s: min={min(durs):.2f} mean={sum(durs)/len(durs):.2f} max={max(durs):.2f} "
              f"p50={durs_s[len(durs_s)//2]:.2f}")
    # what tool was used most
    by_tool = {}
    for e in evs:
        if e.get("type") == "tool_call":
            by_tool[e.get("tool_name", "?")] = by_tool.get(e.get("tool_name", "?"), 0) + 1
    print(f"    tool mix: {sorted(by_tool.items(), key=lambda kv: -kv[1])}")
    # whether bulk fetch was used
    if "fetch_catalog_documents_bulk" in by_tool:
        print(f"    *** BULK FETCH USED: {by_tool['fetch_catalog_documents_bulk']} call(s) ***")
PY
fi
echo "=== monitor / log ==="
LATEST_LOG=$(ls -t ~/archi-bench-v2.*.out 2>/dev/null | head -1)
echo "log: $LATEST_LOG"
tail -20 "$LATEST_LOG"
REMOTE
