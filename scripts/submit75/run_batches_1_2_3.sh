#!/usr/bin/env bash
# Self-driving launcher for batches #1, #2, #3:
#   1. optimized-tools-gemma4-26b-thinking-on (re-run, standalone)
#   2. gemma4 thinking-off (multi[opt,no-tools,rag-only] + bare-llm standalone)
#   3. qwen3.5:27b thinking-on (multi[opt,no-tools,rag-only] + bare-llm standalone)
#
# Each step: deploy, poll until bench container exits, save result, clean up
# ports, then proceed. Failure of one step does NOT abort the rest.
#
# Usage: nohup bash ~/archi/scripts/submit75/run_batches_1_2_3.sh > ~/batch123.log 2>&1 &

set -u

REPO="$HOME/archi"
CFG="$REPO/configs/submit75"
Q="$CFG/curated_questions_categorized.json"
ENV="$CFG/.env"
SAVE="$HOME/bench-results-saved"
LOG="$HOME/batch123-logs"
STATUS="$HOME/batch123.status"

cd "$REPO"
export PATH="$HOME/.local/bin:$PATH"
export BENCH_QUESTION_TIMEOUT=900

mkdir -p "$SAVE" "$LOG"
echo "BATCH123 START $(date -Iseconds)" > "$STATUS"

stop_all_mine() {
  local names
  names=$(podman ps -a --format "{{.Names}}" | grep -v "submit-prod-agent" || true)
  if [ -n "$names" ]; then
    echo "$names" | xargs -r podman stop -t 10 2>&1 | tail -5 || true
    sleep 5
  fi
}

wait_for_bench() {
  local container="$1"
  local max_wait="${2:-36000}"
  local poll=120
  local waited=0
  while true; do
    local st
    st=$(podman inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo missing)
    case "$st" in
      running) ;;
      exited|stopped)
        local rc
        rc=$(podman inspect -f '{{.State.ExitCode}}' "$container" 2>/dev/null || echo "?")
        echo "  container $container exited rc=$rc"
        return "$rc" 2>/dev/null || return 0
        ;;
      missing)
        echo "  container $container missing"
        return 3
        ;;
    esac
    sleep "$poll"
    waited=$((waited + poll))
    if [ "$waited" -ge "$max_wait" ]; then
      echo "  HARD TIMEOUT after ${max_wait}s"
      podman stop -t 10 "$container" 2>/dev/null || true
      return 4
    fi
  done
}

save_result() {
  local container="$1"
  local label="$2"
  podman logs "$container" > "$LOG/${label}-container.log" 2>&1 || true
  local result
  result=$(podman exec "$container" sh -c "ls /root/archi/benchmarks/${label}*.json 2>/dev/null | grep -v checkpoint | tail -1" 2>/dev/null || true)
  if [ -n "$result" ]; then
    podman cp "$container:$result" "$SAVE/" 2>&1 | tail -3 || true
    echo "  saved: $result"
  else
    echo "  WARNING: no result JSON found for $label"
  fi
}

run_eval() {
  local name="$1"
  shift
  local configs="$*"
  local started
  started=$(date -Iseconds)
  echo "=== $name START $started ===" | tee -a "$STATUS"

  stop_all_mine
  local ports
  ports=$(ss -tlnp 2>/dev/null | grep -E ":(5436|7871) " || true)
  if [ -n "$ports" ]; then
    echo "  FAIL: ports still busy after cleanup" | tee -a "$STATUS"
    return 5
  fi

  if ! curl -sf http://localhost:11434/api/tags > /dev/null; then
    echo "  FAIL: ollama not reachable" | tee -a "$STATUS"
    return 2
  fi

  archi evaluate \
    --name "$name" \
    --config $configs \
    --questions "$Q" \
    --hostmode \
    --force \
    --podman \
    --env-file "$ENV" \
    > "$LOG/${name}.log" 2>&1
  local eval_rc=$?
  if [ $eval_rc -ne 0 ]; then
    echo "  FAIL: archi evaluate exit=$eval_rc" | tee -a "$STATUS"
    tail -10 "$LOG/${name}.log" | sed 's/^/    /' | tee -a "$STATUS"
    return $eval_rc
  fi

  wait_for_bench "benchmarking-${name}" 36000
  local bench_rc=$?
  save_result "benchmarking-${name}" "$name"
  echo "DONE $name rc=$bench_rc started=$started finished=$(date -Iseconds)" | tee -a "$STATUS"
  return $bench_rc
}

OVERALL=0

# ── Batch #1: re-run optimized-tools gemma4 thinking-on ──
run_eval "gemma4-on-opt-rerun" \
  "$CFG/eval-optimized-tools-gemma4-26b-thinking-on.yaml" || {
  echo "  (gemma4-on-opt-rerun failed, continuing)" | tee -a "$STATUS"
  OVERALL=1
}
sleep 30

# ── Batch #2a: gemma4 thinking-off multi (3 retrieval configs) ──
run_eval "gemma4-off-multi" \
  "$CFG/eval-optimized-tools-gemma4-26b-thinking-off.yaml,$CFG/eval-no-tools-gemma4-26b-thinking-off.yaml,$CFG/eval-rag-only-gemma4-26b-thinking-off.yaml" || {
  echo "  (gemma4-off-multi failed, continuing)" | tee -a "$STATUS"
  OVERALL=1
}
sleep 30

# ── Batch #2b: gemma4 thinking-off bare-llm (standalone) ──
run_eval "gemma4-off-bare-llm" \
  "$CFG/eval-bare-llm-gemma4-26b-thinking-off.yaml" || {
  echo "  (gemma4-off-bare-llm failed, continuing)" | tee -a "$STATUS"
  OVERALL=1
}
sleep 30

# ── Batch #3a: qwen3.5:27b thinking-on multi (3 retrieval configs) ──
run_eval "qwen27b-on-multi" \
  "$CFG/eval-optimized-tools-qwen3.5-27b-thinking-on.yaml,$CFG/eval-no-tools-qwen3.5-27b-thinking-on.yaml,$CFG/eval-rag-only-qwen3.5-27b-thinking-on.yaml" || {
  echo "  (qwen27b-on-multi failed, continuing)" | tee -a "$STATUS"
  OVERALL=1
}
sleep 30

# ── Batch #3b: qwen3.5:27b thinking-on bare-llm (standalone) ──
run_eval "qwen27b-on-bare-llm" \
  "$CFG/eval-bare-llm-qwen3.5-27b-thinking-on.yaml" || {
  echo "  (qwen27b-on-bare-llm failed, continuing)" | tee -a "$STATUS"
  OVERALL=1
}

echo "" >> "$STATUS"
echo "BATCH123 COMPLETE $(date -Iseconds) overall=$OVERALL" | tee -a "$STATUS"
echo "DONE" > "${STATUS}.done"
exit $OVERALL
