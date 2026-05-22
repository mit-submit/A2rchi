#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== current time ==="
date
echo
echo "=== result file mtime ==="
ls -la ~/bench_out/run_260q_orcd_v3/results_v3_no-tools.json
echo
echo "=== count again ==="
python3 -c "
import json
d = json.load(open('/home/mohoney/bench_out/run_260q_orcd_v3/results_v3_no-tools.json'))
r = d['benchmarking_results'][0]['single_question_results']
errs = sum(1 for v in r.values() if v.get('error'))
print(f'{len(r)}/260  errors={errs}')
"
echo
echo "=== vllm log tail (very recent) ==="
LOG=$(ls -t ~/archi-vllm.14257646.out 2>/dev/null | head -1)
tail -5 "$LOG"
echo
echo "=== bench process state ==="
BENCH_JID=$(squeue -u "$USER" -h -n archi-bench-no-tools -o "%i" 2>/dev/null | head -1)
if [ -n "$BENCH_JID" ]; then
  timeout 8 srun --jobid="$BENCH_JID" --overlap bash -c '
    ps -u mohoney -o pid,etime,stat,pcpu,pmem,cmd | grep -E "python.*run_260q|sleep" | grep -v grep | head -5
    echo --- net connections to node3200:8800 ---
    ss -tan | grep ":8800" | head -5
    echo --- net connections to node1616 ---
    ss -tan | grep "node1616\|10\." | wc -l
  ' 2>/dev/null
fi
REMOTE
