#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== all mohoney processes (filtered) ==="
ps -u mohoney -o pid,ppid,etime,stat,cmd | grep -E "orchestrate|run_260q|monitor_run|bash" | grep -v grep | head -20

echo
echo "=== last 10 lines of orchestrate_27b.log ==="
tail -10 ~/orchestrate_27b.log

echo
echo "=== how many archi-vllm.*.out files exist (each from a job submission)? ==="
ls ~/archi-vllm.*.out 2>/dev/null | wc -l
ls -la ~/archi-vllm.*.out 2>/dev/null | tail -5

echo
echo "=== what's the latest mtime / age of orchestrate_27b.log? ==="
ls -la ~/orchestrate_27b.log
date

echo
echo "=== was live ever attempted? grep launch_4config_inner output in orchestrate_27b.log ==="
grep -E "live|launch_4config" ~/orchestrate_27b.log | tail -10

echo
echo "=== 35B no-tools error breakdown ==="
python3 - <<'PY'
import json, os
from collections import Counter
f = os.path.expanduser("~/bench_out/run_260q_orcd_v3_35b/results_v3_no-tools.json")
d = json.load(open(f))
r = d["benchmarking_results"][0]["single_question_results"]
c = Counter()
for v in r.values():
    e = v.get("error") or ""
    if e:
        c[e.split(":")[0][:50]] += 1
print(f"  total errors: {sum(c.values())}")
for k, n in c.most_common():
    print(f"    {n:4d}  {k}")
PY
REMOTE
