#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M"
echo
echo "=== orchestrator log tail ==="
tail -25 ~/recovery_orchestrator.log
echo
echo "=== archi-services schema reachable? ==="
timeout 5 curl -sS -m 3 http://node1616:7871/api/catalog/schema 2>&1 | head -3
REMOTE
