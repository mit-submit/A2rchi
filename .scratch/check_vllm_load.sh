#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== vllm log tail (last 20 lines) ==="
LOG=$(ls -t ~/archi-vllm.14257646.out 2>/dev/null | head -1)
echo "log: $LOG"
echo "size: $(stat -c %s "$LOG")"
echo "--- last 20 ---"
tail -20 "$LOG"
echo
echo "=== recent throughput samples (last 5 'Avg prompt' lines) ==="
grep "Avg prompt throughput" "$LOG" 2>/dev/null | tail -5
echo
echo "=== bench node loadavg (where no-tools runs) ==="
BENCH_JID=$(squeue -u "$USER" -h -n archi-bench-no-tools -o "%i" 2>/dev/null | head -1)
if [ -n "$BENCH_JID" ]; then
  timeout 8 srun --jobid="$BENCH_JID" --overlap bash -c 'cat /proc/loadavg' 2>/dev/null
fi
echo
echo "=== latest snapshot ==="
cat ~/.bench-hw-snapshot.json 2>/dev/null || echo "no snapshot"
echo
echo "=== orchestrator log tail ==="
tail -8 ~/recovery_orchestrator.log
REMOTE
