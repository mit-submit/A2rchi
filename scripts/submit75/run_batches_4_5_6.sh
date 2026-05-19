#!/usr/bin/env bash
# Queued work to run after run_batches_1_2_3.sh finishes.
#
# Seven archi-evaluate steps in order:
#   #3c: qwen27b-on-multi RESUME — checkpoint has 225/260 done on Config 1 (no-live/think).
#        Picks up at Q226, then runs remaining 2 configs (rag + live) fresh. Uses --resume.
#   #4a: qwen27b-off-multi   (multi[opt,no-tools,rag] thinking-off)
#   #4b: qwen27b-off-bare-llm
#   #5a: qwen122b-on-multi   (thinking-on)
#   #5b: qwen122b-on-bare-llm
#   #6a: qwen122b-off-multi
#   #6b: qwen122b-off-bare-llm
#
# No artificial per-step timeout. Container must exit on its own or be killed
# manually. (The previous script's 10h wait_for_bench ceiling silently killed
# qwen27b-on-multi at 225/780 questions — don't repeat that mistake.)
#
# Usage:
#   nohup bash ~/archi/scripts/submit75/run_batches_4_5_6.sh > ~/batch456.log 2>&1 &
#   disown

set -u

REPO="$HOME/archi"
CFG="$REPO/configs/submit75"
Q="$CFG/curated_questions_categorized.json"
ENV="$CFG/.env"
SAVE="$HOME/bench-results-saved"
LOG="$HOME/batch456-logs"
STATUS="$HOME/batch456.status"
DONE_MARKER_123="$HOME/batch123.status.done"
DONE_MARKER_456="$HOME/batch456.status.done"

cd "$REPO"
export PATH="$HOME/.local/bin:$PATH"
export BENCH_QUESTION_TIMEOUT=1200   # per-question budget (20 min)

mkdir -p "$SAVE" "$LOG"

echo "BATCH456 WAITING for $DONE_MARKER_123 — $(date -Iseconds)" > "$STATUS"

# Poll until batch123 finishes. No ceiling — just wait.
while [ ! -f "$DONE_MARKER_123" ]; do
  sleep 120
done

echo "BATCH456 START $(date -Iseconds)" | tee -a "$STATUS"

# ─────────── helpers ───────────

stop_all_mine() {
  local names
  names=$(podman ps -a --format "{{.Names}}" | grep -v "submit-prod-agent" || true)
  if [ -n "$names" ]; then
    echo "$names" | xargs -r podman stop -t 10 2>&1 | tail -5 || true
    sleep 5
  fi
}

# Wait for a bench container to finish. No hard ceiling — poll indefinitely
# until it either exits on its own or disappears. If you need to abort, kill
# the container manually and this function returns.
wait_for_bench() {
  local container="$1"
  local poll=180
  while true; do
    local st
    st=$(podman inspect -f '{{.State.Status}}' "$container" 2>/dev/null || echo missing)
    case "$st" in
      running)
        ;;
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
  done
}

# bench_out is bind-mounted — the result JSON lands on the host at
# ~/archi/bench_out/benchmarking-<name>-<timestamp>.json. Copy the latest non-
# checkpoint match to bench-results-saved. This sidesteps the podman-exec-on-
# dead-container bug in the original launcher.
save_result_via_host() {
  local name="$1"
  local result
  result=$(ls ~/archi/bench_out/benchmarking-${name}-*.json 2>/dev/null | grep -v checkpoint | tail -1 || true)
  if [ -n "$result" ]; then
    cp "$result" "$SAVE/" 2>&1 | tail -3 || true
    echo "  saved: $(basename "$result")"
  else
    echo "  WARNING: no result JSON found for $name in ~/archi/bench_out"
  fi
  podman logs "benchmarking-${name}" > "$LOG/${name}-container.log" 2>&1 || true
}

# run_eval <name> <configs-comma-separated> [--resume]
run_eval() {
  local name="$1"
  local configs="$2"
  local extra_flags="${3:-}"
  local started
  started=$(date -Iseconds)
  echo "=== $name START $started (flags: ${extra_flags:-none}) ===" | tee -a "$STATUS"

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

  # NB: --force wipes deployment dir AND the checkpoint (see cli_main.py:652).
  # --resume keeps the checkpoint. We use --force for fresh runs and --resume
  # for the qwen27b-on-multi pick-up step. Don't combine them.
  local flags="--force"
  if [ "$extra_flags" = "--resume" ]; then
    flags="--resume"
  fi

  archi evaluate \
    --name "$name" \
    --config "$configs" \
    --questions "$Q" \
    --hostmode \
    $flags \
    --podman \
    --env-file "$ENV" \
    > "$LOG/${name}.log" 2>&1
  local eval_rc=$?
  if [ $eval_rc -ne 0 ]; then
    echo "  FAIL: archi evaluate exit=$eval_rc" | tee -a "$STATUS"
    tail -10 "$LOG/${name}.log" | sed 's/^/    /' | tee -a "$STATUS"
    return $eval_rc
  fi

  wait_for_bench "benchmarking-${name}"
  local bench_rc=$?
  save_result_via_host "$name"
  echo "DONE $name rc=$bench_rc started=$started finished=$(date -Iseconds)" | tee -a "$STATUS"
  return $bench_rc
}

# ─────────── pre-flight ───────────

HAS_122B=0
if curl -sf http://localhost:11434/api/tags | grep -q "qwen3.5:122b-a10b"; then
  HAS_122B=1
  echo "pre-flight: qwen3.5:122b-a10b available" | tee -a "$STATUS"
else
  echo "pre-flight: WARNING — qwen3.5:122b-a10b NOT in Ollama; skipping #5 and #6" | tee -a "$STATUS"
fi

OVERALL=0

# ─────────── #3c: resume qwen27b-on-multi ───────────
# Checkpoint is at ~/archi/bench_out/benchmarking-qwen27b-on-multi.checkpoint.json.
# Resume picks up at Q226 of Config 1 (no-live/think), then runs Configs 2 & 3
# (rag/think and live/think) from scratch.

run_eval "qwen27b-on-multi" \
  "$CFG/eval-optimized-tools-qwen3.5-27b-thinking-on.yaml,$CFG/eval-no-tools-qwen3.5-27b-thinking-on.yaml,$CFG/eval-rag-only-qwen3.5-27b-thinking-on.yaml" \
  "--resume" || {
  echo "  (qwen27b-on-multi resume failed, continuing)" | tee -a "$STATUS"
  OVERALL=1
}
sleep 30

# ─────────── #4: qwen27b no-think ───────────

run_eval "qwen27b-off-multi" \
  "$CFG/eval-optimized-tools-qwen3.5-27b-thinking-off.yaml,$CFG/eval-no-tools-qwen3.5-27b-thinking-off.yaml,$CFG/eval-rag-only-qwen3.5-27b-thinking-off.yaml" || {
  echo "  (qwen27b-off-multi failed, continuing)" | tee -a "$STATUS"
  OVERALL=1
}
sleep 30

run_eval "qwen27b-off-bare-llm" \
  "$CFG/eval-bare-llm-qwen3.5-27b-thinking-off.yaml" || {
  echo "  (qwen27b-off-bare-llm failed, continuing)" | tee -a "$STATUS"
  OVERALL=1
}
sleep 30

# ─────────── #5 / #6: 122b batches (only if model present) ───────────

if [ "$HAS_122B" -eq 1 ]; then
  run_eval "qwen122b-on-multi" \
    "$CFG/eval-optimized-tools-qwen3.5-122b-a10b-thinking-on.yaml,$CFG/eval-no-tools-qwen3.5-122b-a10b-thinking-on.yaml,$CFG/eval-rag-only-qwen3.5-122b-a10b-thinking-on.yaml" || {
    echo "  (qwen122b-on-multi failed, continuing)" | tee -a "$STATUS"
    OVERALL=1
  }
  sleep 30

  run_eval "qwen122b-on-bare-llm" \
    "$CFG/eval-bare-llm-qwen3.5-122b-a10b-thinking-on.yaml" || {
    echo "  (qwen122b-on-bare-llm failed, continuing)" | tee -a "$STATUS"
    OVERALL=1
  }
  sleep 30

  run_eval "qwen122b-off-multi" \
    "$CFG/eval-optimized-tools-qwen3.5-122b-a10b-thinking-off.yaml,$CFG/eval-no-tools-qwen3.5-122b-a10b-thinking-off.yaml,$CFG/eval-rag-only-qwen3.5-122b-a10b-thinking-off.yaml" || {
    echo "  (qwen122b-off-multi failed, continuing)" | tee -a "$STATUS"
    OVERALL=1
  }
  sleep 30

  run_eval "qwen122b-off-bare-llm" \
    "$CFG/eval-bare-llm-qwen3.5-122b-a10b-thinking-off.yaml" || {
    echo "  (qwen122b-off-bare-llm failed, continuing)" | tee -a "$STATUS"
    OVERALL=1
  }
fi

echo "" >> "$STATUS"
echo "BATCH456 COMPLETE $(date -Iseconds) overall=$OVERALL has_122b=$HAS_122B" | tee -a "$STATUS"
echo "DONE" > "$DONE_MARKER_456"
exit $OVERALL
