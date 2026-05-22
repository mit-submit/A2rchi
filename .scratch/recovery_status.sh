#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M %R"
echo
echo "=== recovery_orchestrator.log tail ==="
tail -25 ~/recovery_orchestrator.log 2>/dev/null
echo
echo "=== recovery process alive? ==="
ps -u mohoney -o pid,etime,cmd | grep -E "recovery|orchestrate|monitor_run" | grep -v grep
echo
echo "=== archi-vllm pending reason ==="
LATEST_VLLM_LOG=$(ls -t ~/archi-vllm.*.out 2>/dev/null | head -1)
echo "vllm log: $LATEST_VLLM_LOG"
if [ -f "$LATEST_VLLM_LOG" ]; then
  echo "vllm log size: $(stat -c %s "$LATEST_VLLM_LOG")"
fi
scontrol show job $(squeue -u "$USER" -h -n archi-vllm -o "%i" 2>/dev/null) 2>/dev/null | grep -E "Reason|JobState|Partition" | head -3
echo
echo "=== current archi-services env ==="
cat ~/archi-services.env 2>/dev/null | grep -E "SLURM_JOB_ID|ARCHI_DM_URL|VLLM"
echo
echo "=== catalog reachable? ==="
timeout 5 curl -sS -m 3 ${ARCHI_DM_URL:-http://node1616:7871}/api/catalog/schema 2>&1 | head -3
REMOTE
