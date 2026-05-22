#!/bin/bash
ssh orcd-login bash <<'REMOTE'
# Kill any existing monitor
pkill -f "bash.*monitor_run.sh" 2>/dev/null
sleep 1

# Start new one and verify it sticks
nohup bash ~/monitor_run.sh < /dev/null > /tmp/monitor-stdout.log 2>&1 &
disown
sleep 5
pgrep -af monitor_run.sh
echo "---"
ls -la ~/.bench-hw-snapshot.json 2>&1
echo "---"
cat ~/.bench-hw-snapshot.json 2>&1 | head -3
echo "---"
echo "stdout log:"
cat /tmp/monitor-stdout.log 2>&1 | head -20
REMOTE
