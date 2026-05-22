#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== verify monkey-patch in shipped file ==="
grep -n "_config_access.get_global_config" ~/A2rchi/.scratch/run_260q_orcd_qa.py | head -3
echo
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M"
REMOTE
