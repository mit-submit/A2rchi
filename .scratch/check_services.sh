#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== current queue full ==="
squeue -u "$USER" -o "%i %j %T %M %V %N"
echo
echo "=== archi-services job (any state) ==="
sacct -u "$USER" -j 14213801 --format=JobID,JobName,State,Start,End,ExitCode 2>/dev/null | head -10
echo
echo "=== latest archi-services log ==="
LATEST=$(ls -t ~/archi-services.*.out 2>/dev/null | head -1)
echo "$LATEST  size=$(stat -c %s "$LATEST")"
echo "--- last 30 lines ---"
tail -30 "$LATEST"
echo
echo "=== current archi-services endpoint env ==="
cat ~/archi-services.env 2>/dev/null
echo
echo "=== ping node1616:7871 from login ==="
timeout 5 curl -sS -m 3 http://node1616:7871/api/catalog/schema 2>&1 | head -5
REMOTE
