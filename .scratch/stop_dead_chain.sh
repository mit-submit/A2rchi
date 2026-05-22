#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== killing orchestrators ==="
pkill -f orchestrate_27b_sweep.sh && echo "killed 27b orchestrator" || echo "27b orchestrator not running"
pkill -f orchestrate_gemma_sweeps.sh && echo "killed gemma orchestrator" || echo "gemma orchestrator not running"
echo
echo "=== cancelling pending 27B vllm ==="
JID=$(squeue -u "$USER" -h -n archi-vllm -o "%i" 2>/dev/null | head -1)
if [ -n "$JID" ]; then
  scancel "$JID"
  echo "cancelled $JID"
fi
echo
echo "=== checking required files for archi-services restart ==="
ls -la ~/.archi-bundle-state/bundle 2>/dev/null | head -3
ls -la ~/.archi-bundle-key.txt 2>/dev/null
ls -la ~/archi-deployment-bundle*.tar.zst 2>/dev/null
echo
echo "=== final queue ==="
squeue -u "$USER" -o "%i %j %T"
REMOTE
