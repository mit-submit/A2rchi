#!/bin/bash
# Append the 10 tool-use auxiliary questions to archived 260-question results.
# Runs on orcd-login. It does not rerun existing question_0..question_259 rows:
# both benchmark drivers load the existing output JSON and skip completed qids.
set -euo pipefail

LOG="$HOME/append_aux10_270.log"
exec > >(tee -a "$LOG") 2>&1

ANCHOR_JOB="${1:-}"
QUESTIONS_HOST="$HOME/A2rchi/configs/submit75/curated_questions_270.json"
QUESTIONS_CONTAINER="/workspace/configs/submit75/curated_questions_270.json"
LIMIT=270
QA_CONCURRENCY=10
AGENT_CONCURRENCY=8

log() { printf '[%s] %s\n' "$(date -Iseconds)" "$*"; }

count_qids() {
  local file=$1
  grep -o '"question_[0-9][0-9]*"[[:space:]]*:' "$file" | wc -l | tr -d ' '
}

grep_count() {
  local pattern=$1 file=$2
  (grep -E "$pattern" "$file" || true) | wc -l | tr -d ' '
}

validate_aux_rows() {
  local label=$1 archive_dir=$2
  local ok=0
  for cfg in bare rag no-tools live; do
    local path="$archive_dir/results_v3_${cfg}.json"
    if [ ! -f "$path" ]; then
      echo "$label $cfg: MISSING $path"
      ok=1
      continue
    fi
    local total aux errors budgets
    total=$(count_qids "$path")
    aux=$(grep_count '"question_26[0-9]"[[:space:]]*:' "$path")
    errors=$(grep_count '"error"[[:space:]]*:[[:space:]]*"' "$path")
    budgets=$(grep_count '"hit_budget"[[:space:]]*:[[:space:]]*true' "$path")
    echo "$label $cfg: total=$total aux=$aux/10 errors=$errors budgets=$budgets"
    if [ "$total" -ne 270 ] || [ "$aux" -ne 10 ]; then
      ok=1
    fi
  done
  return "$ok"
}

model_needs_append() {
  local archive_dir=$1
  for cfg in bare rag no-tools live; do
    local path="$archive_dir/results_v3_${cfg}.json"
    if [ ! -f "$path" ]; then
      return 0
    fi
    local total aux
    total=$(count_qids "$path")
    aux=$(grep_count '"question_26[0-9]"[[:space:]]*:' "$path")
    if [ "$total" -lt 270 ] || [ "$aux" -lt 10 ]; then
      return 0
    fi
  done
  return 1
}

wait_for_job_done() {
  local jid=$1 label=$2
  log "waiting for $label job $jid"
  while squeue -j "$jid" -h 2>/dev/null | grep -q .; do
    squeue -j "$jid" -o "%i %j %T %M %N" || true
    sleep 60
  done
  local state
  state=$(sacct -j "$jid" --format=State -n -P 2>/dev/null | head -1 | cut -d'|' -f1 || true)
  log "$label job $jid finished with state=${state:-unknown}"
  if [ "${state:-}" != "COMPLETED" ]; then
    sacct -j "$jid" --format=JobID,JobName%30,State,Elapsed,ExitCode -P 2>/dev/null || true
    return 1
  fi
}

wait_for_named_jobs_clear() {
  local pattern=$1
  while squeue -u "$USER" -h -o "%j" 2>/dev/null | grep -E "$pattern" >/dev/null; do
    squeue -u "$USER" -o "%i %j %T %M %N" || true
    sleep 30
  done
}

restart_services() {
  log "restarting archi-services"
  squeue -u "$USER" -h -o "%i %j" | awk '$2=="archi-services"{print $1}' | while read -r jid; do
    [ -n "$jid" ] && scancel "$jid" || true
  done
  wait_for_named_jobs_clear "^archi-services$"
  local jid
  jid=$(sbatch --parsable \
    --export=ALL,ARCHI_BUNDLE="$HOME/archi-deployment-bundle-20260521.tar.zst",ARCHI_AGE_KEY="$HOME/.archi-bundle-key.txt" \
    "$HOME/A2rchi/scripts/slurm/start_archi_services.sh")
  log "archi-services jid=$jid"
  for _ in $(seq 1 80); do
    if [ -f "$HOME/archi-services.env" ] && grep -q "SLURM_JOB_ID=$jid" "$HOME/archi-services.env"; then
      . "$HOME/archi-services.env"
      if curl -fsS -m 10 "$ARCHI_DM_URL/api/catalog/schema" >/dev/null; then
        log "archi-services ready at $ARCHI_DM_URL"
        return 0
      fi
    fi
    sleep 10
  done
  log "ERROR: archi-services did not become ready"
  return 1
}

ensure_vllm_model() {
  local model=$1 enable_ep=$2 mtp=$3
  if [ -f "$HOME/archi-vllm.env" ]; then
    # shellcheck disable=SC1090
    . "$HOME/archi-vllm.env" || true
    if [ "${VLLM_MODEL:-}" = "$model" ] && curl -fsS -m 10 "$VLLM_URL/models" >/dev/null 2>&1; then
      log "reusing vLLM $model at $VLLM_URL"
      return 0
    fi
  fi

  log "starting vLLM model=$model enable_ep=$enable_ep mtp=$mtp"
  squeue -u "$USER" -h -o "%i %j" | awk '$2=="archi-vllm"{print $1}' | while read -r jid; do
    [ -n "$jid" ] && scancel "$jid" || true
  done
  wait_for_named_jobs_clear "^archi-vllm$"

  local jid
  jid=$(sbatch --parsable \
    --partition=mit_preemptable --gres=gpu:h200:2 \
    --export="ALL,VLLM_MODEL=$model,VLLM_TOOL_CALL_PARSER=qwen3_xml,VLLM_REASONING_PARSER=qwen3,VLLM_MTP_TOKENS=$mtp,VLLM_DISABLE_THINKING=1,VLLM_ENABLE_EXPERT_PARALLEL=$enable_ep,VLLM_TENSOR_PARALLEL=2" \
    "$HOME/A2rchi/scripts/slurm/start_vllm.sh")
  log "vLLM jid=$jid"

  for _ in $(seq 1 100); do
    if [ -f "$HOME/archi-vllm.env" ] \
       && grep -q "SLURM_JOB_ID=$jid" "$HOME/archi-vllm.env" \
       && grep -q "VLLM_MODEL=$model" "$HOME/archi-vllm.env"; then
      # shellcheck disable=SC1090
      . "$HOME/archi-vllm.env"
      if curl -fsS -m 10 "$VLLM_URL/models" >/dev/null; then
        log "vLLM ready at $VLLM_URL"
        return 0
      fi
    fi
    sleep 15
  done
  log "ERROR: vLLM did not become ready for $model"
  sacct -j "$jid" --format=JobID,JobName%30,State,Elapsed,ExitCode -P 2>/dev/null || true
  return 1
}

submit_one_append() {
  local cfg=$1 archive_dir=$2 prev_dep=${3:-}
  local driver concurrency jobname target container_dir dep_arg
  case "$cfg" in
    bare|rag)
      driver=/workspace/.scratch/run_260q_orcd_qa.py
      concurrency=$QA_CONCURRENCY
      ;;
    no-tools|live)
      driver=/workspace/.scratch/run_260q_orcd_v3.py
      concurrency=$AGENT_CONCURRENCY
      ;;
    *) log "ERROR: unknown config $cfg"; return 1 ;;
  esac
  jobname="archi-append-aux10-${MODEL_TAG}-${cfg}"
  target="$archive_dir/results_v3_${cfg}.json"
  container_dir="/bench_out/${archive_dir#"$HOME/bench_out/"}"
  dep_arg=()
  if [ -n "$prev_dep" ]; then
    dep_arg=(--dependency="afterok:$prev_dep")
  fi

  sbatch --parsable "${dep_arg[@]}" <<SBATCH
#!/bin/bash
#SBATCH --job-name=$jobname
#SBATCH --output=$HOME/${jobname}.%j.out
#SBATCH --time=04:00:00
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --nodes=1

set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null
. \$HOME/archi-services.env
. \$HOME/archi-vllm.env

apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  --bind \$HOME/.archi-bundle-state/bundle/secrets/archi:/secrets:ro \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=\$ARCHI_DM_URL \\
  --env ARCHI_RUCIO_MCP_URL=\${ARCHI_RUCIO_MCP_URL:-} \\
  --env VLLM_URL=\$VLLM_URL \\
  --env VLLM_MODEL=\$VLLM_MODEL \\
  --env ORCD_OUT_DIR=/bench_out/run_260q_orcd_v3 \\
  --env MAX_TOOL_CALLS=30 \\
  --env TOOL_TIMEOUT_S=30 \\
  --env PER_QUESTION_TIMEOUT_S=600 \\
  --env CATALOG_HTTP_TIMEOUT_S=20 \\
  --env BULK_FETCH_MAX_HASHES=8 \\
  --env BULK_FETCH_WORKERS=4 \\
  --env CONCURRENCY_OVERRIDE=$concurrency \\
  --env PYTHONPATH=/workspace \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 $driver \\
    --questions $QUESTIONS_CONTAINER \\
    --limit $LIMIT \\
    --tool-set $cfg \\
    --concurrency $concurrency \\
    --out $container_dir/results_v3_${cfg}.json
SBATCH
}

submit_append_model() {
  MODEL_TAG=$1
  local archive_dir=$2
  log "preparing append for $MODEL_TAG in $archive_dir"
  [ -f "$QUESTIONS_HOST" ] || { log "ERROR: missing $QUESTIONS_HOST"; return 1; }
  mkdir -p "$archive_dir"

  local prev=""
  for cfg in bare rag no-tools live; do
    local file="$archive_dir/results_v3_${cfg}.json"
    [ -f "$file" ] || { log "ERROR: missing target $file"; return 1; }
    local n
    n=$(count_qids "$file")
    log "$MODEL_TAG $cfg currently has $n rows"
    if [ "$n" -lt 260 ]; then
      log "ERROR: refusing to append to $file with only $n rows"
      return 1
    fi
    if [ "$n" -ge 270 ]; then
      log "$MODEL_TAG $cfg already has $n rows; skipping"
      continue
    fi
    local backup="${file%.json}.pre_aux10.json"
    if [ ! -f "$backup" ]; then
      cp -p "$file" "$backup"
      log "backed up $file to $backup"
    fi
    local jid
    jid=$(submit_one_append "$cfg" "$archive_dir" "$prev")
    log "submitted $MODEL_TAG $cfg append jid=$jid"
    prev=$jid
  done

  if [ -n "$prev" ]; then
    wait_for_job_done "$prev" "$MODEL_TAG append chain"
  fi
  validate_aux_rows "$MODEL_TAG" "$archive_dir"
}

main() {
  log "append aux10 270 orchestrator starting"
  if [ -n "$ANCHOR_JOB" ]; then
    wait_for_job_done "$ANCHOR_JOB" "anchor"
  fi

  if model_needs_append "$HOME/bench_out/run_260q_orcd_v3_35b"; then
    restart_services
    ensure_vllm_model "Qwen/Qwen3.6-35B-A3B-FP8" 1 1
    submit_append_model "35b" "$HOME/bench_out/run_260q_orcd_v3_35b"
  else
    log "35b archives already have aux10; skipping 35b vLLM startup"
    validate_aux_rows "35b" "$HOME/bench_out/run_260q_orcd_v3_35b"
  fi

  if model_needs_append "$HOME/bench_out/run_260q_orcd_v3_27b"; then
    restart_services
    ensure_vllm_model "Qwen/Qwen3.6-27B-FP8" 0 0
    submit_append_model "27b" "$HOME/bench_out/run_260q_orcd_v3_27b"
  else
    log "27b archives already have aux10; skipping 27b vLLM startup"
    validate_aux_rows "27b" "$HOME/bench_out/run_260q_orcd_v3_27b"
  fi

  log "append aux10 270 orchestrator complete"
}

main "$@"
