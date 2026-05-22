#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M %N"
echo "=== sidecar files ==="
ls -la ~/.bench-hw-snapshot.json ~/.bench-hw-history.jsonl ~/.bench-question-timeline.jsonl 2>&1
echo "=== latest result file ==="
ls -t ~/bench_out/run_260q_orcd/*.json 2>/dev/null | head -1
echo "=== monitor process ==="
pgrep -af monitor_run.sh
echo "=== n_done from snapshot ==="
python3 -c "import json; d=json.load(open('$HOME/.bench-hw-snapshot.json')); print('n_done=', d.get('n_done'))" 2>&1
REMOTE
