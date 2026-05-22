#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== latest no-tools log tail ==="
LOG=$(ls -t ~/archi-bench-no-tools.*.out 2>/dev/null | head -1)
tail -20 "$LOG"
echo
echo "=== sample recent errors from current no-tools result ==="
python3 - <<'PY'
import json, os
from collections import Counter
f = os.path.expanduser("~/bench_out/run_260q_orcd_v3/results_v3_no-tools.json")
d = json.load(open(f))
r = d["benchmarking_results"][0]["single_question_results"]
c = Counter()
for v in r.values():
    e = v.get("error") or ""
    if e:
        c[e.split(":")[0][:80]] += 1
print(f"total: {len(r)}  errors: {sum(c.values())}")
for k, n in c.most_common(8):
    print(f"  {n:4d}  {k}")
# Sample a recent error trace
for qid, v in r.items():
    err = v.get("error") or ""
    if err and v.get("traceback"):
        print(f"\n=== sample error trace ({qid}): ===")
        print(v.get("traceback")[:1500])
        break
PY
echo
echo "=== vllm reachable from login? ==="
curl -sS -m 3 http://node3200:8800/v1/models 2>&1 | head -3
echo
echo "=== catalog reachable? ==="
curl -sS -m 3 http://node1616:7871/api/catalog/schema 2>&1 | head -3
REMOTE
