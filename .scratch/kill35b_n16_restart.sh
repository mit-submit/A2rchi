#!/bin/bash
# Kill 35B live, restart archi-services fresh (cold cache reset),
# wait for it ready, then relaunch recovery orchestrator at N=16
# with skip-35b sentinel.
scp -q /Users/jason/projects/A2rchi/.scratch/recovery_orchestrator.sh orcd-login:recovery_orchestrator.sh
ssh orcd-login bash <<'REMOTE'
set -u
echo "=== killing orchestrator ==="
pkill -f recovery_orchestrator.sh 2>/dev/null
sleep 2

echo "=== cancelling all bench + archi-services (fresh restart) ==="
for J in $(squeue -u "$USER" -h -o "%i %j" | grep -E "archi-bench|archi-services" | awk '{print $1}'); do
  scancel "$J"
  echo "cancelled $J"
done
# leave vllm running
sleep 5

echo "=== queue now ==="
squeue -u "$USER" -o "%i %j %T %M"

echo "=== drop SKIP_35B sentinel ==="
touch ~/.recovery_skip_35b
echo "sentinel: ~/.recovery_skip_35b"

echo "=== rotate orchestrator log ==="
mv -f ~/recovery_orchestrator.log ~/recovery_orchestrator.log.prev3 2>/dev/null

echo "=== relaunching orchestrator (will resubmit archi-services + use N=16) ==="
chmod +x ~/recovery_orchestrator.sh
nohup bash ~/recovery_orchestrator.sh < /dev/null >> /dev/null 2>&1 &
disown
sleep 3
echo "--- process ---"
pgrep -af recovery_orchestrator.sh
echo "--- log tail ---"
tail -15 ~/recovery_orchestrator.log
REMOTE
