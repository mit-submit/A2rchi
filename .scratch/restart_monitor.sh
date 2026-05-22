#!/bin/bash
# Ship new monitor + kill any old monitor + start fresh.
set -eu
scp -q /Users/jason/projects/A2rchi/.scratch/monitor_run.sh orcd-login:monitor_run.sh
ssh orcd-login '
chmod +x ~/monitor_run.sh
pkill -f "bash.*monitor_run.sh" 2>/dev/null || true
sleep 1
nohup bash ~/monitor_run.sh > /dev/null 2>&1 &
disown
sleep 5
echo "monitor pid: $(pgrep -f monitor_run.sh | head -1)"
echo "first snapshot:"
cat ~/.bench-hw-snapshot.json 2>&1 | head -50
'
