#!/bin/bash
set -uo pipefail

ARCHI_DIR=~/archi
CONFIGS_DIR=configs/submit76
ENV_FILE=configs/submit76/.env
LOG_DIR=~/archi/bench_out/logs

mkdir -p "$LOG_DIR"

# Source conda
export PATH="/home/submit/mohoney/.conda/envs/archi311/bin:$PATH"
cd "$ARCHI_DIR"

# Verify archi works
which archi

# Config definitions
declare -A CONFIGS
CONFIGS[copilot-gemma4-26b]="eval-copilot-gemma4-26b.yaml"
CONFIGS[copilot-gpt-oss-120b]="eval-copilot-gpt-oss-120b.yaml"
CONFIGS[copilot-qwen3-32b]="eval-copilot-qwen3-32b.yaml"
CONFIGS[compops-gemma4-26b]="eval-compops-gemma4-26b.yaml"
CONFIGS[compops-gpt-oss-120b]="eval-compops-gpt-oss-120b.yaml"
CONFIGS[compops-qwen3-32b]="eval-compops-qwen3-32b.yaml"

ORDER=(
  copilot-gemma4-26b
  compops-gemma4-26b
  copilot-qwen3-32b
  compops-qwen3-32b
  copilot-gpt-oss-120b
  compops-gpt-oss-120b
)

STATUS_FILE=~/archi/bench_out/eval_status.json
python3 -c "
import json, datetime
d = {
    'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'total_questions': 260,
    'total_configs': 6,
    'run_order': ['copilot-gemma4-26b','compops-gemma4-26b','copilot-qwen3-32b','compops-qwen3-32b','copilot-gpt-oss-120b','compops-gpt-oss-120b'],
    'configs': {}
}
for name in d['run_order']:
    d['configs'][name] = {'status': 'pending'}
with open('$STATUS_FILE', 'w') as f:
    json.dump(d, f, indent=2)
"

echo "========================================"
echo "Starting A2rchi CHEP eval suite"
echo "$(date -u) | ${#ORDER[@]} configs to run"
echo "========================================"

COMPLETED=0
FAILED=0

for name in "${ORDER[@]}"; do
  config="${CONFIGS[$name]}"
  log="$LOG_DIR/${name}.log"
  eval_name="eval-${name}"
  bench_container="benchmarking-${eval_name}"

  echo ""
  echo "========================================"
  echo "[$(date -u)] Starting: $name"
  echo "  Config: $CONFIGS_DIR/$config"
  echo "  Eval name: $eval_name"
  echo "  Benchmark container: $bench_container"
  echo "  Log: $log"
  echo "========================================"

  # Update status to running
  python3 -c "
import json, datetime
with open('$STATUS_FILE') as f: d = json.load(f)
d['configs']['$name'] = {
    'status': 'running',
    'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'config_file': '$config'
}
d['updated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
with open('$STATUS_FILE', 'w') as f: json.dump(d, f, indent=2)
"

  START_TIME=$(date +%s)

  # Clean up any previous containers for this eval
  podman stop -a 2>/dev/null
  podman rm -a 2>/dev/null
  sleep 2

  # Always use --force to ensure all containers (postgres, data-manager,
  # benchmarking) are started fresh. Without --force, archi evaluate only
  # restarts the benchmarking container against a stale deployment.
  # Launch the eval (starts containers and returns)
  echo "[$(date -u)] Launching archi evaluate..."
  archi evaluate \
    -n "$eval_name" \
    -c "$CONFIGS_DIR/$config" \
    -e "$ENV_FILE" \
    --hostmode -p --force -v 2 \
    2>&1 | tee -a "$log"

  echo "[$(date -u)] Eval command returned. Waiting for benchmark container..."

  # Wait for the benchmarking container to finish
  # Poll every 30s until the container exits
  MAX_WAIT=28800  # 8 hours max per config
  WAITED=0
  POLL_INTERVAL=30

  while [ $WAITED -lt $MAX_WAIT ]; do
    # Check container status
    CONTAINER_STATUS=$(podman inspect --format '{{.State.Status}}' "$bench_container" 2>/dev/null || echo "not_found")

    if [ "$CONTAINER_STATUS" = "exited" ]; then
      EXIT_CODE=$(podman inspect --format '{{.State.ExitCode}}' "$bench_container" 2>/dev/null || echo "999")
      echo "[$(date -u)] Benchmark container exited with code: $EXIT_CODE"

      # Save container logs
      podman logs "$bench_container" >> "$log" 2>&1

      # Update checkpoint progress from container output
      # The benchmark writes its output inside the container at /root/archi/benchmarks/
      # which is mounted to bench_out/ on the host

      break
    elif [ "$CONTAINER_STATUS" = "running" ]; then
      # Update status with checkpoint progress
      python3 -c "
import json, datetime, glob, os
try:
    cp_file = 'bench_out/benchmarking-${eval_name}.checkpoint.json'
    q_done = 0
    if os.path.exists(cp_file):
        with open(cp_file) as f:
            cp = json.load(f)
        ip = cp.get('in_progress', {})
        q_done = ip.get('question_id', 0)
    with open('$STATUS_FILE') as f: d = json.load(f)
    d['configs']['$name']['questions_done'] = q_done
    d['configs']['$name']['status'] = 'running'
    d['updated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open('$STATUS_FILE', 'w') as f: json.dump(d, f, indent=2)
except Exception as e:
    print(f'Status update error: {e}')
" 2>/dev/null

      sleep $POLL_INTERVAL
      WAITED=$((WAITED + POLL_INTERVAL))
    elif [ "$CONTAINER_STATUS" = "not_found" ]; then
      echo "[$(date -u)] Container not found yet, waiting..."
      sleep 10
      WAITED=$((WAITED + 10))
    else
      echo "[$(date -u)] Unknown container status: $CONTAINER_STATUS"
      sleep $POLL_INTERVAL
      WAITED=$((WAITED + POLL_INTERVAL))
    fi
  done

  END_TIME=$(date +%s)
  ELAPSED=$((END_TIME - START_TIME))

  if [ "$CONTAINER_STATUS" = "exited" ] && [ "$EXIT_CODE" = "0" ]; then
    COMPLETED=$((COMPLETED + 1))
    python3 -c "
import json, datetime
with open('$STATUS_FILE') as f: d = json.load(f)
d['configs']['$name']['status'] = 'completed'
d['configs']['$name']['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
d['configs']['$name']['elapsed_seconds'] = $ELAPSED
d['configs']['$name']['questions_done'] = 260
d['updated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
with open('$STATUS_FILE', 'w') as f: json.dump(d, f, indent=2)
"
    echo "[$(date -u)] DONE: $name (${ELAPSED}s)"
  else
    FAILED=$((FAILED + 1))
    python3 -c "
import json, datetime
with open('$STATUS_FILE') as f: d = json.load(f)
d['configs']['$name']['status'] = 'failed'
d['configs']['$name']['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
d['configs']['$name']['elapsed_seconds'] = $ELAPSED
d['configs']['$name']['exit_code'] = '${EXIT_CODE:-unknown}'
d['updated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
with open('$STATUS_FILE', 'w') as f: json.dump(d, f, indent=2)
"
    echo "[$(date -u)] FAILED: $name (${ELAPSED}s, exit=${EXIT_CODE:-unknown})"
  fi

  # Cleanup containers before next run
  echo "[$(date -u)] Cleaning up containers..."
  podman stop -a 2>/dev/null
  podman rm -a 2>/dev/null
  sleep 2

done

echo ""
echo "========================================"
echo "All evals finished: $COMPLETED completed, $FAILED failed"
echo "$(date -u)"
echo "========================================"

python3 -c "
import json, datetime
with open('$STATUS_FILE') as f: d = json.load(f)
d['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
d['summary'] = {'completed': $COMPLETED, 'failed': $FAILED, 'total': ${#ORDER[@]}}
with open('$STATUS_FILE', 'w') as f: json.dump(d, f, indent=2)
"
