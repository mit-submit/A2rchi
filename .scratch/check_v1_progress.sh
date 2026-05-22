#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M %N"
echo "=== v1 progress ==="
LATEST=$(ls -t ~/bench_out/run_260q_orcd/*.json 2>/dev/null | head -1)
if [ -n "$LATEST" ]; then
  python3 - "$LATEST" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
r = d["benchmarking_results"][0]["single_question_results"]
print(f"file: {sys.argv[1]}")
print(f"progress: {len(r)}/260")
PY
fi
echo "=== v2 file present ==="
ls -la ~/A2rchi/.scratch/run_260q_orcd_v2.py
REMOTE
