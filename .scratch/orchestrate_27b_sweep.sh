#!/bin/bash
# Autonomous orchestrator. Run on orcd-login via nohup. Steps:
#   1. Wait for the current 35B sweep (live = last in chain) to leave the queue.
#   2. Archive 35B results to ~/bench_out/run_260q_orcd_v3_35b/.
#   3. Cancel the 35B vllm job.
#   4. Start a 27B vllm job (VLLM_MODEL=Qwen/Qwen3.6-27B-FP8).
#   5. Poll until vllm's /v1/models responds.
#   6. Update ~/archi-vllm.env to point at the new vllm + model.
#   7. Submit the 4-config sweep again (uses launch_4config_inner.sh).
#
# Everything logs to ~/orchestrate_27b.log. Tail with:
#   ssh orcd-login 'tail -f ~/orchestrate_27b.log'
set -uo pipefail

LOG=$HOME/orchestrate_27b.log
exec >>"$LOG" 2>&1

log() { echo "[$(date -Iseconds)] $*"; }

log "=== orchestrate_27b_sweep STARTED ==="
log "shell pid=$$ user=$USER"

# Source endpoints so we know what the 35B sweep is using
. $HOME/archi-services.env
. $HOME/archi-vllm.env
log "current VLLM_MODEL=$VLLM_MODEL  VLLM_URL=$VLLM_URL"

# ---------- 1. Wait for 35B sweep to finish ----------
log "waiting for archi-bench-live to leave queue…"
while squeue -u "$USER" -h -n archi-bench-live -o "%T" 2>/dev/null | grep -q .; do
  S=$(squeue -u "$USER" -h -n archi-bench-live -o "%T %M" 2>/dev/null)
  log "  live still queued: $S"
  sleep 60
done
# also wait for any other bench-* job (no-tools etc)
while squeue -u "$USER" -h -o "%j" 2>/dev/null | grep -q "^archi-bench-"; do
  log "  other archi-bench job(s) still queued: $(squeue -u "$USER" -h -o "%i %j %T" | grep '^[^ ]* archi-bench-')"
  sleep 60
done
log "all archi-bench-* jobs are gone"

# ---------- 2. Archive 35B results ----------
log "archiving 35B results to ~/bench_out/run_260q_orcd_v3_35b/"
mkdir -p $HOME/bench_out/run_260q_orcd_v3_35b
cp -r $HOME/bench_out/run_260q_orcd_v3/results_v3_*.json $HOME/bench_out/run_260q_orcd_v3_35b/ 2>/dev/null || true
log "  archived: $(ls -la $HOME/bench_out/run_260q_orcd_v3_35b/ | wc -l) files"
ls -la $HOME/bench_out/run_260q_orcd_v3_35b/ | tee -a "$LOG"

# Clear the live dir so 27B starts fresh
rm -f $HOME/bench_out/run_260q_orcd_v3/results_v3_*.json
log "  cleared live dir for 27B run"

# ---------- 3. Cancel 35B vllm ----------
log "cancelling 35B archi-vllm job"
VLLM_OLD_JID=$(squeue -u "$USER" -h -n archi-vllm -o "%i" 2>/dev/null | head -1)
if [ -n "$VLLM_OLD_JID" ]; then
  scancel "$VLLM_OLD_JID"
  log "  scancelled vllm jid=$VLLM_OLD_JID"
  # wait for it to actually go away
  while squeue -u "$USER" -h -n archi-vllm -o "%T" 2>/dev/null | grep -q .; do
    log "  vllm still in queue: $(squeue -u $USER -h -n archi-vllm -o '%T %M')"
    sleep 10
  done
  log "  35B vllm gone"
else
  log "  no archi-vllm job in queue (already gone?)"
fi

# ---------- 4. Start 27B vllm ----------
log "submitting 27B vllm (Qwen/Qwen3.6-27B-FP8)"
NEW_VLLM_JID=$(sbatch --parsable \
  --export=ALL,VLLM_MODEL=Qwen/Qwen3.6-27B-FP8 \
  $HOME/A2rchi/scripts/slurm/start_vllm.sh)
log "  submitted: $NEW_VLLM_JID"

# ---------- 5. Wait for new vllm to be ready ----------
log "polling for new vllm /v1/models …"
NEW_NODE=""
NEW_PORT=""
for i in $(seq 1 90); do  # up to 90 * 30s = 45 min
  # Find the node the new vllm is on
  STATE=$(squeue -u "$USER" -h -j "$NEW_VLLM_JID" -o "%T %N" 2>/dev/null)
  log "  iter $i: $STATE"
  NODE=$(echo "$STATE" | awk '{print $2}')
  if [ -n "$NODE" ] && [ "$NODE" != "(null)" ]; then
    # Try to ping vllm's models endpoint
    # Port is in the start_vllm.sh; default 8800. We'll discover via the vllm log.
    LATEST_VLLM_LOG=$(ls -t $HOME/archi-vllm.*.out 2>/dev/null | head -1)
    if [ -f "$LATEST_VLLM_LOG" ]; then
      PORT=$(grep -oE 'Uvicorn running on http://0.0.0.0:[0-9]+' "$LATEST_VLLM_LOG" | tail -1 | grep -oE '[0-9]+$')
      if [ -n "$PORT" ]; then
        URL="http://$NODE:$PORT/v1"
        if curl -sS -m 5 "$URL/models" 2>/dev/null | grep -q "Qwen3.6-27B"; then
          NEW_NODE=$NODE
          NEW_PORT=$PORT
          log "  vllm READY at $URL"
          break
        fi
      fi
    fi
  fi
  sleep 30
done

if [ -z "$NEW_NODE" ] || [ -z "$NEW_PORT" ]; then
  log "FATAL: 27B vllm never came up. abort. inspect ~/archi-vllm.*.out"
  exit 2
fi

# ---------- 6. Update ~/archi-vllm.env ----------
NEW_URL="http://$NEW_NODE:$NEW_PORT/v1"
NEW_MODEL="Qwen/Qwen3.6-27B-FP8"
log "updating ~/archi-vllm.env to URL=$NEW_URL MODEL=$NEW_MODEL"
cat > $HOME/archi-vllm.env <<ENV
export VLLM_URL=$NEW_URL
export VLLM_MODEL=$NEW_MODEL
ENV
cat $HOME/archi-vllm.env | tee -a "$LOG"

# ---------- 7. Submit 4-config sweep for 27B ----------
log "submitting 4-config 27B sweep via launch_4config_inner.sh"
export ARCHI_LIMIT=260
export ARCHI_CONCURRENCY=32
export ARCHI_MAX_TOOL_CALLS=30
export ARCHI_TOOL_TIMEOUT_S=30
export ARCHI_PER_QUESTION_TIMEOUT_S=600
bash $HOME/A2rchi/.scratch/launch_4config_inner.sh | tee -a "$LOG"

log "=== 27B sweep submitted; now waiting for it to finish for archival ==="

# ---------- 8. Wait for 27B sweep to finish + archive ----------
sleep 60   # give sbatch time to register
while squeue -u "$USER" -h -o "%j" 2>/dev/null | grep -q "^archi-bench-"; do
  Q=$(squeue -u "$USER" -h -o "%i %j %T %M" | grep "archi-bench-")
  log "  27B sweep still running: $Q"
  sleep 120
done

log "27B sweep finished; archiving to ~/bench_out/run_260q_orcd_v3_27b/"
mkdir -p $HOME/bench_out/run_260q_orcd_v3_27b
cp -r $HOME/bench_out/run_260q_orcd_v3/results_v3_*.json $HOME/bench_out/run_260q_orcd_v3_27b/ 2>/dev/null || true
ls -la $HOME/bench_out/run_260q_orcd_v3_27b/ | tee -a "$LOG"

log "=== orchestrate_27b_sweep ALL DONE ==="
