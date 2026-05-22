#!/bin/bash
scp -q /Users/jason/projects/A2rchi/.scratch/recovery_orchestrator.sh orcd-login:recovery_orchestrator.sh
scp -q /Users/jason/projects/A2rchi/.scratch/launch_4config_inner.sh orcd-login:A2rchi/.scratch/launch_4config_inner.sh
ssh orcd-login bash <<'REMOTE'
echo "=== kill orchestrator + pending 35B vllm ==="
pkill -f recovery_orchestrator.sh 2>/dev/null
sleep 2
JID=$(squeue -u "$USER" -h -n archi-vllm -o "%i" 2>/dev/null | head -1)
if [ -n "$JID" ]; then scancel "$JID"; echo "cancelled vllm $JID"; fi

echo "=== queue now ==="
squeue -u "$USER" -o "%i %j %T %M"

echo "=== rotate orchestrator log + restart ==="
mv -f ~/recovery_orchestrator.log ~/recovery_orchestrator.log.prev5 2>/dev/null
chmod +x ~/recovery_orchestrator.sh
nohup bash ~/recovery_orchestrator.sh < /dev/null >> /dev/null 2>&1 &
disown
sleep 3
echo "--- process ---"
pgrep -af recovery_orchestrator.sh
echo "--- log tail ---"
tail -15 ~/recovery_orchestrator.log
REMOTE
