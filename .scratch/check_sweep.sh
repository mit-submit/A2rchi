#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M %R"
echo
echo "=== result files (any so far) ==="
ls -la ~/bench_out/run_260q_orcd_v3/results_v3_*.json 2>/dev/null || echo "(none yet)"
echo
echo "=== bare progress (if started) ==="
F=~/bench_out/run_260q_orcd_v3/results_v3_bare.json
if [ -f "$F" ]; then
  python3 - "$F" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
r = d["benchmarking_results"][0]["single_question_results"]
errs = sum(1 for v in r.values() if v.get("error"))
print(f"bare: {len(r)}/260  errors={errs}")
PY
fi
echo
echo "=== latest bare log tail ==="
LATEST=$(ls -t ~/archi-bench-bare.*.out 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
  echo "log: $LATEST"
  tail -10 "$LATEST"
else
  echo "(no bare log yet)"
fi
REMOTE
