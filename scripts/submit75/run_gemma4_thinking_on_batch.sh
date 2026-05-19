#!/usr/bin/env bash
# Self-driving launcher for the gemma4:26b thinking-on batch (4 configs).
#
# Runs sequentially: bare-llm → rag-only → no-tools → optimized-tools.
# Each config writes its own log and the result JSON to ~/archi/bench_out/.
# Failure of one config does NOT abort the rest — we want as much data
# as possible by morning.
#
# Designed to be launched via nohup and left running. Polls submit75
# Ollama health and bench container state between configs.
#
# Usage on submit75:
#
#   nohup bash ~/archi/scripts/submit75/run_gemma4_thinking_on_batch.sh \
#     > ~/gemma4-batch.log 2>&1 &

set -u  # not -e: we want to keep going on individual failures

REPO_DIR="$HOME/archi"
LOG_DIR="$HOME/gemma4-batch-logs"
STATUS_FILE="$HOME/gemma4-batch.status"
CONFIGS_DIR="$REPO_DIR/configs/submit75"
QUESTIONS_PATH="$CONFIGS_DIR/curated_questions_categorized.json"
ENV_FILE="$CONFIGS_DIR/.env"
EVAL_NAME_PREFIX="gemma4-think-on"

# Run order: cheapest/safest first. bare-llm has no data sources so its DM
# rebuild is fast. The 3 retrieval-using configs all rebuild the same DM.
RUN_ORDER=(
  "bare-llm"
  "rag-only"
  "no-tools"
  "optimized-tools"
)

mkdir -p "$LOG_DIR"
echo "START $(date -Iseconds)" > "$STATUS_FILE"
echo "host: $(hostname)" >> "$STATUS_FILE"
echo "configs: ${RUN_ORDER[*]}" >> "$STATUS_FILE"
echo "log_dir: $LOG_DIR" >> "$STATUS_FILE"
echo "" >> "$STATUS_FILE"

cd "$REPO_DIR"
export PATH="$HOME/.local/bin:$PATH"
export BENCH_QUESTION_TIMEOUT=900   # 15 min per question hard cap

run_one_config() {
  local cond="$1"
  local config_path="$CONFIGS_DIR/eval-${cond}-gemma4-26b-thinking-on.yaml"
  local eval_name="${EVAL_NAME_PREFIX}-${cond}"
  local cfg_log="$LOG_DIR/${cond}.log"
  local started
  started=$(date -Iseconds)

  echo "=== $cond START $started ===" | tee -a "$STATUS_FILE"

  if [ ! -f "$config_path" ]; then
    echo "FAIL $cond: config not found at $config_path" | tee -a "$STATUS_FILE"
    return 1
  fi

  # Stop any previous eval containers (don't touch prod *-submit-prod-agent-v2)
  podman stop -t 5 \
    "benchmarking-${eval_name}" \
    "postgres-${eval_name}" \
    "data-manager-${eval_name}" 2>/dev/null || true

  # Verify Ollama is reachable
  if ! curl -sf http://localhost:11434/api/tags > /dev/null; then
    echo "FAIL $cond: Ollama not reachable at localhost:11434" | tee -a "$STATUS_FILE"
    return 2
  fi

  # Launch eval (blocks until the deployment is started, container runs
  # in background under podman). The bench container exits when done.
  archi evaluate \
    --name "$eval_name" \
    --config "$config_path" \
    --questions "$QUESTIONS_PATH" \
    --hostmode \
    --force \
    --podman \
    --env-file "$ENV_FILE" \
    > "$cfg_log" 2>&1
  local eval_rc=$?
  if [ $eval_rc -ne 0 ]; then
    echo "FAIL $cond: archi evaluate exit=$eval_rc (see $cfg_log)" | tee -a "$STATUS_FILE"
    return $eval_rc
  fi

  # Poll the bench container until it exits or hits a hard timeout (8 hours).
  local container="benchmarking-${eval_name}"
  local max_wait=$((8 * 60 * 60))
  local waited=0
  local poll_interval=60
  while true; do
    local status
    status=$(podman inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo "missing")
    case "$status" in
      running)
        ;;
      exited|stopped)
        local rc
        rc=$(podman inspect -f '{{.State.ExitCode}}' "$container" 2>/dev/null || echo "?")
        local finished
        finished=$(date -Iseconds)
        echo "DONE $cond exit=$rc started=$started finished=$finished" | tee -a "$STATUS_FILE"
        # Copy bench container logs to log dir
        podman logs "$container" > "$LOG_DIR/${cond}-container.log" 2>&1 || true
        return $rc
        ;;
      missing)
        echo "FAIL $cond: container disappeared (status=missing)" | tee -a "$STATUS_FILE"
        return 3
        ;;
      *)
        ;;
    esac
    sleep "$poll_interval"
    waited=$((waited + poll_interval))
    if [ "$waited" -ge "$max_wait" ]; then
      echo "FAIL $cond: hard timeout after ${max_wait}s" | tee -a "$STATUS_FILE"
      podman stop -t 10 "$container" 2>/dev/null || true
      return 4
    fi
  done
}

OVERALL_RC=0
for cond in "${RUN_ORDER[@]}"; do
  run_one_config "$cond" || {
    rc=$?
    echo "  ($cond returned $rc, continuing to next config)" | tee -a "$STATUS_FILE"
    OVERALL_RC=$rc
  }
  # Small breathing room between configs so Ollama can drop the previous
  # model's KV cache cleanly.
  sleep 30
done

echo "" >> "$STATUS_FILE"
echo "BATCH COMPLETE $(date -Iseconds)" | tee -a "$STATUS_FILE"
echo "overall_rc=$OVERALL_RC" | tee -a "$STATUS_FILE"
echo "DONE" > "${STATUS_FILE}.done"
exit $OVERALL_RC
