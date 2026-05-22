#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M"
echo
echo "=== check patched file on ORCD ==="
grep -n "class HTTPHybridVectorstore" ~/A2rchi/.scratch/run_260q_orcd_qa.py
grep -n "_VectorStore" ~/A2rchi/.scratch/run_260q_orcd_qa.py | head -3
echo
echo "=== mtime ==="
ls -la ~/A2rchi/.scratch/run_260q_orcd_qa.py
REMOTE
