#!/bin/bash
# Ship the patched recovery_orchestrator.sh, cancel pending vllm,
# and re-nohup.
scp -q /Users/jason/projects/A2rchi/.scratch/recovery_orchestrator.sh orcd-login:recovery_orchestrator.sh
ssh orcd-login bash <<'REMOTE'
echo "=== cancelling stuck pending vllm ==="
JID=$(squeue -u "$USER" -h -n archi-vllm -o "%i" 2>/dev/null | head -1)
if [ -n "$JID" ]; then
  scancel "$JID"
  echo "cancelled vllm jid=$JID"
fi
echo
echo "=== killing prior recovery orchestrator if any ==="
pkill -f recovery_orchestrator.sh 2>/dev/null
sleep 1
echo
echo "=== rotating old recovery log ==="
mv -f ~/recovery_orchestrator.log ~/recovery_orchestrator.log.prev 2>/dev/null
echo
echo "=== launching patched recovery orchestrator ==="
chmod +x ~/recovery_orchestrator.sh
nohup bash ~/recovery_orchestrator.sh < /dev/null >> /dev/null 2>&1 &
disown
sleep 3
echo "--- process ---"
pgrep -af recovery_orchestrator.sh
echo "--- log tail ---"
tail -15 ~/recovery_orchestrator.log
REMOTE
