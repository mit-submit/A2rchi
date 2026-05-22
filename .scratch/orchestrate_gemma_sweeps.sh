#!/bin/bash
# Orchestrate the Gemma4 sweeps after the 27B sweep finishes.
# Models in order: google/gemma-4-31B-it, google/gemma-4-26B-A4B-it
#
# Run on orcd-login via nohup. Logs to ~/orchestrate_gemma.log.
#
# Preconditions:
#   - orchestrate_27b_sweep.sh has completed (we wait for its log line "ALL DONE")
#   - HUGGING_FACE_HUB_TOKEN env var set (Gemma is gated on HF) — if missing
#     we skip with a clear log and exit nonzero so the user can rerun after
#     adding the token.

set -uo pipefail
LOG=$HOME/orchestrate_gemma.log
exec >>"$LOG" 2>&1
log() { echo "[$(date -Iseconds)] $*"; }

log "=== orchestrate_gemma_sweeps STARTED ==="

# ---------- 1. Wait for 27B orchestrator to be done ----------
log "waiting for ~/orchestrate_27b.log to show 'ALL DONE'…"
for i in $(seq 1 720); do  # up to 720 * 60s = 12 hours
  if grep -q "orchestrate_27b_sweep ALL DONE" $HOME/orchestrate_27b.log 2>/dev/null; then
    log "  27B orchestrator finished"
    break
  fi
  sleep 60
done
if ! grep -q "orchestrate_27b_sweep ALL DONE" $HOME/orchestrate_27b.log 2>/dev/null; then
  log "FATAL: 27B orchestrator never finished after 12h wait. abort."
  exit 1
fi

# ---------- 2. Verify HF_TOKEN is available ----------
HF_TOKEN_FILE=$HOME/.archi-bundle-state/bundle/secrets/archi/hf_token.txt
if [ -f "$HF_TOKEN_FILE" ]; then
  export HUGGING_FACE_HUB_TOKEN=$(cat "$HF_TOKEN_FILE")
  log "loaded HUGGING_FACE_HUB_TOKEN from $HF_TOKEN_FILE"
fi
if [ -z "${HUGGING_FACE_HUB_TOKEN:-}" ]; then
  log "FATAL: HUGGING_FACE_HUB_TOKEN is not set. Gemma4 is gated on HF and"
  log "       cannot be downloaded. Add token to $HF_TOKEN_FILE then rerun."
  exit 2
fi

# ---------- helper: run one model's 4-config sweep ----------
run_one_model_sweep() {
  local model_id=$1       # full HF id, e.g. google/gemma-4-31B-it
  local archive_dir=$2    # e.g. ~/bench_out/run_260q_orcd_v3_gemma31b/
  local enable_ep=$3      # 1 for MoE, 0 for dense

  log
  log "======================================================="
  log "STARTING SWEEP for $model_id"
  log "  archive will land at: $archive_dir"
  log "======================================================="

  # 1. Cancel current vllm
  VLLM_OLD_JID=$(squeue -u "$USER" -h -n archi-vllm -o "%i" 2>/dev/null | head -1)
  if [ -n "$VLLM_OLD_JID" ]; then
    log "cancelling current vllm job $VLLM_OLD_JID"
    scancel "$VLLM_OLD_JID"
    while squeue -u "$USER" -h -n archi-vllm -o "%T" 2>/dev/null | grep -q .; do
      sleep 10
    done
  fi

  # 2. Submit new vllm with Gemma-appropriate flags
  log "submitting vllm for $model_id"
  NEW_VLLM_JID=$(sbatch --parsable \
    --export=ALL,VLLM_MODEL="$model_id",VLLM_TOOL_CALL_PARSER=gemma4,VLLM_REASONING_PARSER=,VLLM_MTP_TOKENS=0,VLLM_DISABLE_THINKING=0,VLLM_ENABLE_EXPERT_PARALLEL=$enable_ep,HUGGING_FACE_HUB_TOKEN="$HUGGING_FACE_HUB_TOKEN" \
    $HOME/A2rchi/scripts/slurm/start_vllm.sh)
  log "  submitted vllm: $NEW_VLLM_JID"

  # 3. Wait for vllm to be ready (up to 45 min)
  log "polling for vllm ready (up to 45 min)…"
  NEW_NODE=""
  NEW_PORT=""
  for i in $(seq 1 90); do
    STATE=$(squeue -u "$USER" -h -j "$NEW_VLLM_JID" -o "%T %N" 2>/dev/null)
    NODE=$(echo "$STATE" | awk '{print $2}')
    if [ -z "$STATE" ]; then
      log "  iter $i: vllm job no longer in queue — check ~/archi-vllm.$NEW_VLLM_JID.out"
      tail -30 $HOME/archi-vllm.$NEW_VLLM_JID.out 2>/dev/null | sed 's/^/    /'
      log "SKIP: vllm failed to start for $model_id"
      return 1
    fi
    log "  iter $i: $STATE"
    if [ -n "$NODE" ] && [ "$NODE" != "(null)" ]; then
      LATEST_VLLM_LOG=$HOME/archi-vllm.$NEW_VLLM_JID.out
      if [ -f "$LATEST_VLLM_LOG" ]; then
        PORT=$(grep -oE 'Uvicorn running on http://0.0.0.0:[0-9]+' "$LATEST_VLLM_LOG" | tail -1 | grep -oE '[0-9]+$')
        if [ -n "$PORT" ]; then
          URL="http://$NODE:$PORT/v1"
          if curl -sS -m 5 "$URL/models" 2>/dev/null | grep -q "$(basename $model_id)"; then
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
    log "SKIP: $model_id vllm never came up. inspect ~/archi-vllm.$NEW_VLLM_JID.out"
    return 2
  fi

  # 4. Update ~/archi-vllm.env (start_vllm.sh writes it but with /v1 path — confirm)
  if ! grep -q "$model_id" $HOME/archi-vllm.env 2>/dev/null; then
    log "  rewriting archi-vllm.env"
    cat > $HOME/archi-vllm.env <<ENV
export VLLM_URL=http://$NEW_NODE:$NEW_PORT/v1
export VLLM_MODEL=$model_id
ENV
  fi
  cat $HOME/archi-vllm.env

  # 5. Clear live results dir
  rm -f $HOME/bench_out/run_260q_orcd_v3/results_v3_*.json
  log "  cleared live results dir for sweep"

  # 6. Submit 4-config sweep
  log "  submitting 4-config sweep…"
  export ARCHI_LIMIT=260
  export ARCHI_CONCURRENCY=32
  export ARCHI_MAX_TOOL_CALLS=30
  export ARCHI_TOOL_TIMEOUT_S=30
  export ARCHI_PER_QUESTION_TIMEOUT_S=600
  bash $HOME/A2rchi/.scratch/launch_4config_inner.sh 2>&1 | tee -a "$LOG"

  # 7. Wait for sweep to finish
  sleep 60
  while squeue -u "$USER" -h -o "%j" 2>/dev/null | grep -q "^archi-bench-"; do
    Q=$(squeue -u "$USER" -h -o "%i %j %T %M" | grep "archi-bench-")
    log "  sweep still running: $Q"
    sleep 120
  done
  log "  sweep done for $model_id"

  # 8. Archive
  log "  archiving to $archive_dir"
  mkdir -p "$archive_dir"
  cp -r $HOME/bench_out/run_260q_orcd_v3/results_v3_*.json "$archive_dir/" 2>/dev/null || true
  ls -la "$archive_dir" | tee -a "$LOG"
}

# ---------- 3. Gemma 4 31B dense ----------
run_one_model_sweep "google/gemma-4-31B-it" "$HOME/bench_out/run_260q_orcd_v3_gemma4-31b/" 0 || log "Gemma4-31B sweep skipped/failed; continuing"

# ---------- 4. Gemma 4 26B MoE ----------
run_one_model_sweep "google/gemma-4-26B-A4B-it" "$HOME/bench_out/run_260q_orcd_v3_gemma4-26b/" 1 || log "Gemma4-26B sweep skipped/failed; continuing"

log "=== orchestrate_gemma_sweeps ALL DONE ==="
