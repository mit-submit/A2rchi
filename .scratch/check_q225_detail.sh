#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== current snapshot ==="
cat ~/.bench-hw-snapshot.json
echo
echo
echo "=== bench python proc on bench node ==="
BENCH_JID=$(squeue -u "$USER" -h -n archi-bench-v2 -o "%i" 2>/dev/null | head -1)
timeout 8 srun --jobid="$BENCH_JID" --overlap bash -c '
  ps -u mohoney -o pid,etime,pcpu,pmem,cmd | grep -E "python|run_260q" | grep -v grep | head -5
' 2>/dev/null
echo
echo "=== monitor log tail (n_done over time) ==="
tail -15 ~/bench-monitor.log
echo
echo "=== recent vllm request lines (last 5) ==="
VLLM_LOG=$(ls -t ~/archi-vllm.*.out 2>/dev/null | head -1)
grep "INFO" "$VLLM_LOG" | tail -5
REMOTE
