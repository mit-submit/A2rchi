#!/bin/bash
# Poll until the archi-bench-qa job finishes (max ~3 min).
for i in $(seq 1 36); do
  STATE=$(ssh orcd-login "squeue -u \$USER -h -n archi-bench-qa -o '%T' 2>/dev/null" | head -1)
  if [ -z "$STATE" ]; then
    echo "iter $i: job left queue"
    break
  fi
  echo "iter $i: $STATE"
  sleep 5
done
echo "---"
bash /Users/jason/projects/A2rchi/.scratch/check_rag_smoke.sh
