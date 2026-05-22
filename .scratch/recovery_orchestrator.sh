#!/bin/bash
# Recovery orchestrator after archi-services TIMEOUT killed everything mid-run.
#
# State on entry:
#   - archi-services dead (12h timeout)
#   - vllm killed
#   - 35B bare ✓ done, rag ✓ done in ~/bench_out/run_260q_orcd_v3_35b/
#   - 35B no-tools corrupt (89/260 timeouts in ~/bench_out/run_260q_orcd_v3_35b/)
#   - 35B live never ran (instant connection-refused)
#   - 27B + gemma not started
#
# Plan:
#   1. Restart archi-services (12h walltime — reaches ~16:30 EDT)
#   2. Restart vllm with 35B
#   3. Re-run 35B no-tools + live only
#   4. Archive final 35B → run_260q_orcd_v3_35b/
#   5. Restart vllm with 27B
#   6. Run 27B all 4 configs
#   7. Archive 27B
#   8. (if HF_TOKEN) Gemma4-31B sweep, archive
#   9. (if HF_TOKEN) Gemma4-26B-A4B sweep, archive
#
# Run via: nohup bash recovery_orchestrator.sh < /dev/null > /dev/null 2>&1 &

set -uo pipefail
LOG=$HOME/recovery_orchestrator.log
exec >>"$LOG" 2>&1
log() { echo "[$(date -Iseconds)] $*" >&2; }

log "=== recovery_orchestrator STARTED ==="

# Required for restarting archi-services
export ARCHI_BUNDLE=$HOME/archi-deployment-bundle-20260521.tar.zst
export ARCHI_AGE_KEY=$HOME/.archi-bundle-key.txt
[ -f "$ARCHI_BUNDLE" ] || { log "FATAL: ARCHI_BUNDLE missing"; exit 1; }
[ -f "$ARCHI_AGE_KEY" ] || { log "FATAL: ARCHI_AGE_KEY missing"; exit 1; }

# ---------- helper: wait for a job to become RUNNING + a curl check passes ----------
wait_for_jid_running() {
  local jid=$1 max=$2 label=$3
  log "  waiting for $label (job $jid) to be RUNNING (up to $((max*30))s)…"
  for i in $(seq 1 $max); do
    STATE=$(squeue -u "$USER" -h -j "$jid" -o "%T" 2>/dev/null)
    if [ -z "$STATE" ]; then
      log "  iter $i: job $jid left queue unexpectedly"
      sacct -u "$USER" -j "$jid" --format=JobID,State,ExitCode 2>/dev/null | head -5 | sed 's/^/    /'
      return 1
    fi
    if [ "$STATE" = "RUNNING" ]; then
      log "  iter $i: $label RUNNING"
      return 0
    fi
    log "  iter $i: $STATE"
    sleep 30
  done
  log "  TIMED OUT waiting for $label"
  return 2
}

wait_for_url() {
  local url=$1 max=$2 label=$3
  log "  polling $url for $label (up to $((max*15))s)…"
  for i in $(seq 1 $max); do
    if curl -fsS -m 5 "$url" 2>/dev/null >/dev/null; then
      log "  iter $i: $label is up"
      return 0
    fi
    sleep 15
  done
  log "  TIMED OUT waiting for $url"
  return 2
}

# ---------- 1. Restart archi-services (or reuse if already running) ----------
EXISTING_SERV_JID=$(squeue -u "$USER" -h -n archi-services -o "%i" 2>/dev/null | head -1)
if [ -n "$EXISTING_SERV_JID" ]; then
  log "[1] archi-services already running (jid=$EXISTING_SERV_JID); reusing"
  SERV_JID=$EXISTING_SERV_JID
else
  log "[1] submitting archi-services restart"
  SERV_JID=$(sbatch --parsable \
    --export=ALL,ARCHI_BUNDLE=$ARCHI_BUNDLE,ARCHI_AGE_KEY=$ARCHI_AGE_KEY \
    $HOME/A2rchi/scripts/slurm/start_archi_services.sh)
  log "  submitted archi-services: $SERV_JID"
  wait_for_jid_running "$SERV_JID" 30 "archi-services" || { log "FATAL: archi-services failed to start"; exit 2; }
fi

# Wait for archi-services.env to be written with this jid
log "  waiting for archi-services.env to show jid=$SERV_JID…"
for i in $(seq 1 60); do
  if [ -f "$HOME/archi-services.env" ] && grep -q "SLURM_JOB_ID=$SERV_JID" "$HOME/archi-services.env"; then
    log "  archi-services.env shows SLURM_JOB_ID=$SERV_JID"
    break
  fi
  sleep 10
done
. $HOME/archi-services.env
log "  ARCHI_DM_URL=$ARCHI_DM_URL"

# Wait for catalog endpoint to be reachable
wait_for_url "$ARCHI_DM_URL/api/catalog/schema" 40 "archi data-manager" || { log "FATAL: archi data-manager unreachable"; exit 3; }

# ---------- helper: start vllm for a given model + flags ----------
# Submit to mit_preemptable with 2 H200s (matches the original 35B vllm).
# Sets a GLOBAL `LAST_VLLM_JID` instead of echoing — calling in $(…)
# would put the env-file source in a subshell and not propagate VLLM_URL.
# Reuses an existing vllm if it's already serving the requested model.
LAST_VLLM_JID=""
source_vllm_env_for() {
  local jid=$1 model=$2
  if [ ! -f "$HOME/archi-vllm.env" ]; then
    log "  ERROR: $HOME/archi-vllm.env is missing for vllm jid=$jid"
    sacct -u "$USER" -j "$jid" --format=JobID,State,ExitCode 2>/dev/null | head -8 | sed 's/^/    /'
    return 1
  fi
  if ! grep -q "SLURM_JOB_ID=$jid" "$HOME/archi-vllm.env"; then
    log "  ERROR: $HOME/archi-vllm.env does not point to jid=$jid"
    sed 's/^/    /' "$HOME/archi-vllm.env"
    return 1
  fi
  if ! grep -q "VLLM_MODEL=$model" "$HOME/archi-vllm.env"; then
    log "  ERROR: $HOME/archi-vllm.env does not point to model=$model"
    sed 's/^/    /' "$HOME/archi-vllm.env"
    return 1
  fi
  . "$HOME/archi-vllm.env"
}

start_vllm_for() {
  local model=$1 enable_ep=$2 tool_parser=$3 reasoning_parser=$4 disable_thinking=$5 mtp=$6

  # Reuse existing vllm if it's serving this model
  local existing_jid
  existing_jid=$(squeue -u "$USER" -h -n archi-vllm -o "%i" 2>/dev/null | head -1)
  if [ -n "$existing_jid" ] && [ -f "$HOME/archi-vllm.env" ]; then
    if grep -q "SLURM_JOB_ID=$existing_jid" "$HOME/archi-vllm.env" \
       && grep -q "VLLM_MODEL=$model" "$HOME/archi-vllm.env"; then
      log "  reusing existing vllm for $model (jid=$existing_jid)"
      LAST_VLLM_JID=$existing_jid
      source_vllm_env_for "$existing_jid" "$model" || return 1
      return 0
    fi
    log "  existing vllm is wrong model; cancelling jid=$existing_jid"
    scancel "$existing_jid"
    while squeue -u "$USER" -h -n archi-vllm -o "%T" 2>/dev/null | grep -q .; do sleep 10; done
  fi

  log "  starting vllm: model=$model parser=$tool_parser ep=$enable_ep mtp=$mtp"
  local export_args="ALL,VLLM_MODEL=$model,VLLM_TOOL_CALL_PARSER=$tool_parser,VLLM_REASONING_PARSER=$reasoning_parser,VLLM_MTP_TOKENS=$mtp,VLLM_DISABLE_THINKING=$disable_thinking,VLLM_ENABLE_EXPERT_PARALLEL=$enable_ep,VLLM_TENSOR_PARALLEL=2"
  if [ -n "${HUGGING_FACE_HUB_TOKEN:-}" ]; then
    export_args="$export_args,HUGGING_FACE_HUB_TOKEN=$HUGGING_FACE_HUB_TOKEN"
  fi
  local jid
  jid=$(sbatch --parsable \
    --partition=mit_preemptable --gres=gpu:h200:2 \
    --export="$export_args" $HOME/A2rchi/scripts/slurm/start_vllm.sh)
  log "  vllm jid=$jid"
  wait_for_jid_running "$jid" 30 "vllm($model)" || return 1

  log "  waiting for $HOME/archi-vllm.env to point to new vllm…"
  local env_ready=0
  for i in $(seq 1 60); do
    if grep -q "SLURM_JOB_ID=$jid" $HOME/archi-vllm.env 2>/dev/null; then
      env_ready=1
      break
    fi
    sleep 10
  done
  if [ "$env_ready" != "1" ]; then
    log "  ERROR: timed out waiting for $HOME/archi-vllm.env for jid=$jid"
    sacct -u "$USER" -j "$jid" --format=JobID,State,ExitCode 2>/dev/null | head -8 | sed 's/^/    /'
    return 1
  fi
  source_vllm_env_for "$jid" "$model" || return 1
  log "  VLLM_URL=$VLLM_URL  VLLM_MODEL=$VLLM_MODEL"
  wait_for_url "$VLLM_URL/models" 40 "vllm /v1/models" || return 2
  LAST_VLLM_JID=$jid
  return 0
}

# Track vllm jid so we can cancel it later
CUR_VLLM_JID=""

# ---------- 2. Start vllm with 35B (skip if SKIP_35B sentinel) ----------
if [ -f "$HOME/.recovery_skip_35b" ]; then
  log "[2] SKIPPED 35B vllm start (sentinel ~/.recovery_skip_35b)"
else
  log "[2] starting vllm with 35B"
  start_vllm_for "Qwen/Qwen3.6-35B-A3B-FP8" 1 qwen3_xml qwen3 1 1 || {
    log "FATAL: 35B vllm failed to start"; exit 4;
  }
  CUR_VLLM_JID=$LAST_VLLM_JID
  log "  35B vllm ready at $VLLM_URL (jid=$CUR_VLLM_JID)"
fi

# ---------- 3. (SKIPPED on restart) Re-run 35B no-tools + live ----------
# User killed 35B sweep because of catalog saturation; rerun produced 80%+
# errors. We keep whatever's in the live dir (corrupt) for analysis but
# don't re-run. To re-enable, remove the SKIP_35B sentinel below.
if [ -f "$HOME/.recovery_skip_35b" ]; then
  log "[3] SKIPPED 35B reruns (sentinel ~/.recovery_skip_35b)"
else
log "[3] rerunning 35B no-tools + live"
# Clear stale results in the live dir (run_260q_orcd_v3/)
rm -f $HOME/bench_out/run_260q_orcd_v3/results_v3_*.json
# Submit only no-tools and live for this run
submit_pair() {
  local driver=$1 j1=$2 j2=$3
  # use launch_4config_inner.sh but tweak: only submit no-tools + live
  # Instead of forking it, inline the two sbatches here.
  cat > /tmp/run_${j1}.sbatch <<SBATCH
#!/bin/bash
#SBATCH --job-name=archi-bench-${j1}
#SBATCH --output=$HOME/archi-bench-${j1}.%j.out
#SBATCH --time=08:00:00
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --nodes=1
set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null
mkdir -p \$HOME/bench_out/run_260q_orcd_v3
apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  --bind \$HOME/.archi-bundle-state/bundle/secrets/archi:/secrets:ro \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=$ARCHI_DM_URL \\
  --env ARCHI_RUCIO_MCP_URL=$ARCHI_RUCIO_MCP_URL \\
  --env VLLM_URL=$VLLM_URL \\
  --env VLLM_MODEL=$VLLM_MODEL \\
  --env ORCD_OUT_DIR=/bench_out/run_260q_orcd_v3 \\
  --env MAX_TOOL_CALLS=30 --env TOOL_TIMEOUT_S=30 --env PER_QUESTION_TIMEOUT_S=600 \\
  --env CONCURRENCY_OVERRIDE=32 --env PYTHONPATH=/workspace \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/.scratch/$driver \\
    --limit 260 --tool-set ${j1} --concurrency 32 --max-tool-calls 30 \\
    --out /bench_out/run_260q_orcd_v3/results_v3_${j1}.json
SBATCH
  if [ -n "$j2" ]; then
    cat > /tmp/run_${j2}.sbatch <<SBATCH
#!/bin/bash
#SBATCH --job-name=archi-bench-${j2}
#SBATCH --output=$HOME/archi-bench-${j2}.%j.out
#SBATCH --time=08:00:00
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --nodes=1
set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null
mkdir -p \$HOME/bench_out/run_260q_orcd_v3
apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  --bind \$HOME/.archi-bundle-state/bundle/secrets/archi:/secrets:ro \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=$ARCHI_DM_URL \\
  --env ARCHI_RUCIO_MCP_URL=$ARCHI_RUCIO_MCP_URL \\
  --env VLLM_URL=$VLLM_URL \\
  --env VLLM_MODEL=$VLLM_MODEL \\
  --env ORCD_OUT_DIR=/bench_out/run_260q_orcd_v3 \\
  --env MAX_TOOL_CALLS=30 --env TOOL_TIMEOUT_S=30 --env PER_QUESTION_TIMEOUT_S=600 \\
  --env CONCURRENCY_OVERRIDE=32 --env PYTHONPATH=/workspace \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/.scratch/$driver \\
    --limit 260 --tool-set ${j2} --concurrency 32 --max-tool-calls 30 \\
    --out /bench_out/run_260q_orcd_v3/results_v3_${j2}.json
SBATCH
  fi
}

# build sbatch files for no-tools (v3 driver) and live (v3 driver)
submit_pair run_260q_orcd_v3.py no-tools live

JOB_NOTOOLS=$(sbatch --parsable /tmp/run_no-tools.sbatch)
log "  submitted no-tools: $JOB_NOTOOLS"
JOB_LIVE=$(sbatch --parsable --dependency=afterany:$JOB_NOTOOLS /tmp/run_live.sbatch)
log "  submitted live: $JOB_LIVE (after $JOB_NOTOOLS)"

# Wait for both to finish
log "  waiting for both 35B reruns to complete…"
sleep 60
while squeue -u "$USER" -h -o "%j" 2>/dev/null | grep -q "^archi-bench-"; do
  Q=$(squeue -u "$USER" -h -o "%i %j %T %M" | grep '^[^ ]* archi-bench-')
  log "  still running: $Q"
  sleep 180
done
log "  35B no-tools + live complete"
fi  # end of !skip_35b block

# ---------- 4. Archive 35B (overwrite if exists since we now have full set) ----------
log "[4] archiving 35B results"
mkdir -p $HOME/bench_out/run_260q_orcd_v3_35b
# Copy fresh no-tools + live; keep existing bare + rag
cp -f $HOME/bench_out/run_260q_orcd_v3/results_v3_no-tools.json $HOME/bench_out/run_260q_orcd_v3_35b/ 2>/dev/null
cp -f $HOME/bench_out/run_260q_orcd_v3/results_v3_live.json $HOME/bench_out/run_260q_orcd_v3_35b/ 2>/dev/null
ls -la $HOME/bench_out/run_260q_orcd_v3_35b/

# ---------- helper: full 4-config sweep using launch_4config_inner.sh ----------
run_full_4config_sweep() {
  local model_tag=$1 archive_dir=$2
  log "  clearing live dir for $model_tag sweep"
  rm -f $HOME/bench_out/run_260q_orcd_v3/results_v3_*.json

  log "  submitting 4-config sweep (using launch_4config_inner.sh)"
  export ARCHI_LIMIT=270 ARCHI_CONCURRENCY=16 ARCHI_MAX_TOOL_CALLS=30
  export ARCHI_QUESTIONS_PATH=/workspace/configs/submit75/curated_questions_270.json
  export ARCHI_TOOL_TIMEOUT_S=30 ARCHI_PER_QUESTION_TIMEOUT_S=600
  bash $HOME/A2rchi/.scratch/launch_4config_inner.sh 2>&1 | sed 's/^/    /'

  log "  waiting for $model_tag sweep to finish…"
  sleep 60
  while squeue -u "$USER" -h -o "%j" 2>/dev/null | grep -q "^archi-bench-"; do
    sleep 180
  done
  log "  $model_tag sweep finished"

  log "  archiving to $archive_dir"
  mkdir -p "$archive_dir"
  cp -f $HOME/bench_out/run_260q_orcd_v3/results_v3_*.json "$archive_dir/" 2>/dev/null || true
  ls -la "$archive_dir"
}

# ---------- 5. Switch vllm to 27B ----------
log "[5] switching vllm to 27B"
if [ -n "$CUR_VLLM_JID" ]; then
  scancel "$CUR_VLLM_JID"
  while squeue -u "$USER" -h -n archi-vllm -o "%T" 2>/dev/null | grep -q .; do sleep 10; done
fi

# Qwen3.6-27B is dense (no MoE) — disable expert parallel (enable_ep=0).
# Also disable MTP (Qwen3 MTP needs MoE structure; dense model doesn't accept it).
start_vllm_for "Qwen/Qwen3.6-27B-FP8" 0 qwen3_xml qwen3 1 0 || {
  log "FATAL: 27B vllm failed to start"; exit 5;
}
CUR_VLLM_JID=$LAST_VLLM_JID

# ---------- 6. Run 27B sweep ----------
log "[6] 27B 4-config sweep"
run_full_4config_sweep "27b" "$HOME/bench_out/run_260q_orcd_v3_27b/"

# ---------- 7. Switch + Gemma sweeps (require HF_TOKEN) ----------
HF_TOKEN_FILE=$HOME/.archi-bundle-state/bundle/secrets/archi/hf_token.txt
if [ -f "$HF_TOKEN_FILE" ]; then
  export HUGGING_FACE_HUB_TOKEN=$(cat "$HF_TOKEN_FILE")
  log "loaded HF token from $HF_TOKEN_FILE"
fi
if [ -n "${HUGGING_FACE_HUB_TOKEN:-}" ]; then
  log "[7] Gemma4-31B sweep"
  scancel "$CUR_VLLM_JID"
  while squeue -u "$USER" -h -n archi-vllm -o "%T" 2>/dev/null | grep -q .; do sleep 10; done
  if start_vllm_for "google/gemma-4-31B-it" 0 gemma4 "" 0 0; then
    CUR_VLLM_JID=$LAST_VLLM_JID
    run_full_4config_sweep "gemma4-31b" "$HOME/bench_out/run_260q_orcd_v3_gemma4-31b/"
  else
    log "SKIP: Gemma4-31B vllm failed to start"
  fi

  log "[8] Gemma4-26B-A4B sweep"
  scancel "$CUR_VLLM_JID"
  while squeue -u "$USER" -h -n archi-vllm -o "%T" 2>/dev/null | grep -q .; do sleep 10; done
  if start_vllm_for "google/gemma-4-26B-A4B-it" 1 gemma4 "" 0 0; then
    CUR_VLLM_JID=$LAST_VLLM_JID
    run_full_4config_sweep "gemma4-26b" "$HOME/bench_out/run_260q_orcd_v3_gemma4-26b/"
  else
    log "SKIP: Gemma4-26B vllm failed to start"
  fi
else
  log "[7,8] SKIPPED: HUGGING_FACE_HUB_TOKEN not set — Gemma sweeps need HF auth"
  log "  add token at $HF_TOKEN_FILE and re-run this orchestrator"
fi

log "=== recovery_orchestrator ALL DONE ==="
