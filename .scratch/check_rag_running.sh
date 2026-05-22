#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== rag job ==="
squeue -u "$USER" -j 14249513 -o "%i %j %T %M"
echo
echo "=== rag log tail ==="
LATEST=$(ls -t ~/archi-bench-qa.*.out 2>/dev/null | head -1)
RAG_LOG=$(ls -t ~/archi-bench-rag.*.out 2>/dev/null | head -1)
echo "rag-job log: $RAG_LOG"
tail -15 "$RAG_LOG"
echo
echo "=== rag progress ==="
F=~/bench_out/run_260q_orcd_v3/results_v3_rag.json
if [ -f "$F" ]; then
  python3 - "$F" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
r = d["benchmarking_results"][0]["single_question_results"]
errs = sum(1 for v in r.values() if v.get("error"))
nh = sum(len(v.get("sources_metadata") or []) for v in r.values())
mean_hits = (nh / len(r)) if r else 0
print(f"rag: {len(r)}/260  errors={errs}  mean_hits={mean_hits:.1f}")
PY
fi
REMOTE
