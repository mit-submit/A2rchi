#!/bin/bash
# Ship and start the 27B orchestrator on orcd-login.
scp -q /Users/jason/projects/A2rchi/.scratch/orchestrate_27b_sweep.sh orcd-login:orchestrate_27b_sweep.sh
ssh orcd-login bash <<'REMOTE'
chmod +x ~/orchestrate_27b_sweep.sh
# Kill any prior orchestrator
pkill -f orchestrate_27b_sweep.sh 2>/dev/null
sleep 1
nohup bash ~/orchestrate_27b_sweep.sh < /dev/null >> /dev/null 2>&1 &
disown
sleep 3
echo "--- orchestrator process ---"
pgrep -af orchestrate_27b_sweep.sh
echo "--- log tail ---"
tail -10 ~/orchestrate_27b.log 2>/dev/null
REMOTE
