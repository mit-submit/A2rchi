#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M %V"
echo
echo "=== orchestrate_27b.log tail ==="
tail -30 ~/orchestrate_27b.log 2>/dev/null
echo
echo "=== orchestrate_gemma.log tail ==="
tail -15 ~/orchestrate_gemma.log 2>/dev/null
echo
echo "=== results inventory ==="
for d in run_260q_orcd_v3 run_260q_orcd_v3_35b run_260q_orcd_v3_27b run_260q_orcd_v3_gemma4-31b run_260q_orcd_v3_gemma4-26b; do
  if [ -d ~/bench_out/$d ]; then
    echo "  ~/bench_out/$d/:"
    ls -la ~/bench_out/$d/results_v3_*.json 2>/dev/null | awk '{print "    " $5 " " $6 " " $7 " " $8 " " $9}'
  fi
done
echo
echo "=== per-config completion ==="
python3 - <<'PY'
import json, os, glob, statistics
for d in sorted(glob.glob(os.path.expanduser("~/bench_out/run_260q_orcd_v3*"))):
    tag = os.path.basename(d).replace("run_260q_orcd_v3_", "") or "live"
    if tag == "run_260q_orcd_v3": tag = "live(uncommitted)"
    for f in sorted(glob.glob(os.path.join(d, "results_v3_*.json"))):
        try:
            data = json.load(open(f))
            r = data["benchmarking_results"][0]["single_question_results"]
            errs = sum(1 for v in r.values() if v.get("error"))
            times = [v.get("time_elapsed", 0) for v in r.values()]
            tools = [v.get("n_tool_calls", sum(1 for e in v.get("trace_events", []) if e.get("type") == "tool_call")) for v in r.values()]
            mean_t = statistics.mean(times) if times else 0
            mean_tools = statistics.mean(tools) if tools else 0
            cfg = os.path.basename(f).replace("results_v3_", "").replace(".json", "")
            print(f"  [{tag}] {cfg}: {len(r)}/260  errs={errs}  mean_time={mean_t:.1f}s  mean_tools={mean_tools:.1f}")
        except Exception as e:
            print(f"  [{tag}] {os.path.basename(f)}: ERROR reading: {e}")
PY
echo
echo "=== orchestrator pids ==="
pgrep -af 'orchestrate_27b\|orchestrate_gemma'
REMOTE
