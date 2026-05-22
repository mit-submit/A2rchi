#!/bin/bash
ssh orcd-login bash <<'REMOTE'
LATEST=$(ls -t ~/bench_out/run_260q_orcd_v2/*.json 2>/dev/null | head -1)
python3 - "$LATEST" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
r = d["benchmarking_results"][0]["single_question_results"]
done = set(r.keys())
all_qids = {f"question_{i}" for i in range(260)}
missing = sorted(all_qids - done, key=lambda s: int(s.split("_")[1]))
print(f"completed: {len(done)}/260")
print(f"missing:   {missing}")
PY
echo
echo "=== log tail (look for STARTs without matching DONEs) ==="
LATEST_LOG=$(ls -t ~/archi-bench-v2.*.out 2>/dev/null | head -1)
grep -E "(START|DONE) question_" "$LATEST_LOG" | tail -10
REMOTE
