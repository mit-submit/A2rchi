#!/bin/bash
# Launch the recovery orchestrator without touching vllm.
ssh orcd-login bash <<'REMOTE'
pkill -f recovery_orchestrator.sh 2>/dev/null
sleep 1
mv -f ~/recovery_orchestrator.log ~/recovery_orchestrator.log.prev2 2>/dev/null
chmod +x ~/recovery_orchestrator.sh
nohup bash ~/recovery_orchestrator.sh < /dev/null >> /dev/null 2>&1 &
disown
sleep 3
echo "--- process ---"
pgrep -af recovery_orchestrator.sh
echo "--- log tail ---"
tail -20 ~/recovery_orchestrator.log
REMOTE
