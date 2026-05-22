#!/bin/bash
ssh orcd-login bash <<'REMOTE'
set -u

echo "=== killing orchestrator ==="
pkill -f recovery_orchestrator.sh 2>/dev/null
sleep 2

echo "=== killing pending 27B vllm (will resubmit cleanly after services up) ==="
JID=$(squeue -u "$USER" -h -n archi-vllm -o "%i" 2>/dev/null | head -1)
if [ -n "$JID" ]; then scancel "$JID"; echo "cancelled vllm $JID"; fi

echo "=== waiting for old archi-services to fully die ==="
for i in $(seq 1 60); do
  S=$(squeue -u "$USER" -h -n archi-services -o "%T" 2>/dev/null)
  if [ -z "$S" ]; then echo "  archi-services gone"; break; fi
  echo "  iter $i: archi-services still $S"
  sleep 10
done

echo "=== submitting FRESH archi-services ==="
NEW_JID=$(sbatch --parsable \
  --export=ALL,ARCHI_BUNDLE=$HOME/archi-deployment-bundle-20260521.tar.zst,ARCHI_AGE_KEY=$HOME/.archi-bundle-key.txt \
  $HOME/A2rchi/scripts/slurm/start_archi_services.sh)
echo "submitted: $NEW_JID"

echo "=== waiting for archi-services RUNNING + catalog ready ==="
for i in $(seq 1 60); do
  S=$(squeue -u "$USER" -h -j "$NEW_JID" -o "%T %N" 2>/dev/null)
  if [ -z "$S" ]; then echo "  FAILED: $NEW_JID left queue"; exit 1; fi
  echo "  iter $i: $S"
  if [[ "$S" == "RUNNING"* ]]; then
    NODE=$(echo "$S" | awk '{print $2}')
    if [ -n "$NODE" ] && curl -fsS -m 3 "http://$NODE:7871/api/catalog/schema" >/dev/null 2>&1; then
      echo "  catalog reachable at http://$NODE:7871"
      break
    fi
  fi
  sleep 10
done

echo "=== confirm archi-services.env points to new archi-services ==="
for i in $(seq 1 30); do
  if [ -f "$HOME/archi-services.env" ] && grep -q "SLURM_JOB_ID=$NEW_JID" "$HOME/archi-services.env"; then
    cat "$HOME/archi-services.env"
    break
  fi
  echo "  iter $i: waiting for archi-services.env to update"
  sleep 5
done

echo "=== rotate orchestrator log + restart orchestrator ==="
mv -f ~/recovery_orchestrator.log ~/recovery_orchestrator.log.prev4 2>/dev/null
nohup bash ~/recovery_orchestrator.sh < /dev/null >> /dev/null 2>&1 &
disown
sleep 3
echo "--- orchestrator process ---"
pgrep -af recovery_orchestrator.sh
echo "--- log tail ---"
tail -15 ~/recovery_orchestrator.log
REMOTE
