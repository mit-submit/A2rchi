#!/bin/bash
# Ship + nohup the recovery orchestrator on ORCD.
scp -q /Users/jason/projects/A2rchi/.scratch/recovery_orchestrator.sh orcd-login:recovery_orchestrator.sh
ssh orcd-login bash <<'REMOTE'
chmod +x ~/recovery_orchestrator.sh
pkill -f recovery_orchestrator.sh 2>/dev/null
sleep 1
nohup bash ~/recovery_orchestrator.sh < /dev/null >> /dev/null 2>&1 &
disown
sleep 3
echo "--- recovery process ---"
pgrep -af recovery_orchestrator.sh
echo "--- log tail ---"
tail -20 ~/recovery_orchestrator.log 2>/dev/null
REMOTE
