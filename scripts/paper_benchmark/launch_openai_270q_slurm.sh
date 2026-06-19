#!/bin/bash
# Inner script run on orcd-login: submit the four GPT-5.5 270Q configs.
# No vLLM/GPU job is needed; rag/no-tools/live still use archi-services.
set -euo pipefail

SERVICES_ENV_FILE="${ARCHI_SERVICES_ENV_FILE:-$HOME/archi-services.env}"
INITIAL_DEPENDENCY="${ARCHI_INITIAL_DEPENDENCY:-}"
INITIAL_DEPENDENCY_TYPE="${ARCHI_INITIAL_DEPENDENCY_TYPE:-afterany}"

if [ -f "$SERVICES_ENV_FILE" ]; then
  . "$SERVICES_ENV_FILE"
  [ -n "${ARCHI_DM_URL:-}" ] || { echo "ERROR: ARCHI_DM_URL is unset after sourcing $SERVICES_ENV_FILE" >&2; exit 3; }
  [ -n "${ARCHI_POSTGRES_URL:-}" ] || { echo "ERROR: ARCHI_POSTGRES_URL is unset after sourcing $SERVICES_ENV_FILE" >&2; exit 3; }
elif [ -n "$INITIAL_DEPENDENCY" ] && [ "$INITIAL_DEPENDENCY_TYPE" = "after" ]; then
  echo "INFO: $SERVICES_ENV_FILE is not present yet; sbatch jobs will wait for it after service job $INITIAL_DEPENDENCY starts." >&2
else
  echo "ERROR: missing $SERVICES_ENV_FILE" >&2
  exit 2
fi

SECRET_FILE="$HOME/.archi-bundle-state/bundle/secrets/archi/openai_api_key.txt"
[ -s "$SECRET_FILE" ] || { echo "ERROR: missing $SECRET_FILE; write openai_api_key.txt under the Archi secrets directory first" >&2; exit 4; }

MODEL="${ARCHI_OPENAI_MODEL:-gpt-5.5-2026-04-23}"
LIMIT="${ARCHI_LIMIT:-270}"
START="${ARCHI_START:-0}"
QUESTIONS_PATH="${ARCHI_QUESTIONS_PATH:-/workspace/configs/submit75/curated_questions_270.json}"
OUT_SUBDIR="${ARCHI_OUT_SUBDIR:-run_270q_gpt55_openai}"
JOB_LABEL="${ARCHI_JOB_LABEL:-gpt55}"
JOB_LABEL="$(printf '%s' "$JOB_LABEL" | tr -c 'A-Za-z0-9_.-' '-' | cut -c1-32)"
[ -n "$JOB_LABEL" ] || JOB_LABEL="openai"
QA_CONCURRENCY="${ARCHI_QA_CONCURRENCY:-8}"
AGENT_CONCURRENCY="${ARCHI_AGENT_CONCURRENCY:-4}"
MAX_TOOL_CALLS="${ARCHI_MAX_TOOL_CALLS:-30}"
TOOL_TIMEOUT_S="${ARCHI_TOOL_TIMEOUT_S:-30}"
LLM_TIMEOUT_S="${ARCHI_LLM_TIMEOUT_S:-200}"
PER_QUESTION_TIMEOUT_S="${ARCHI_PER_QUESTION_TIMEOUT_S:-600}"
CATALOG_HTTP_TIMEOUT_S="${ARCHI_CATALOG_HTTP_TIMEOUT_S:-20}"
BULK_FETCH_MAX_HASHES="${ARCHI_BULK_FETCH_MAX_HASHES:-8}"
BULK_FETCH_WORKERS="${ARCHI_BULK_FETCH_WORKERS:-4}"
if [ -z "${OPENAI_REASONING_EFFORT:-}" ]; then
  OPENAI_REASONING_EFFORT="high"
fi
if [ -z "${OPENAI_USE_RESPONSES_API:-}" ]; then
  USE_RESPONSES_API="1"
else
  USE_RESPONSES_API="$OPENAI_USE_RESPONSES_API"
fi
CONFIGS="${ARCHI_CONFIGS:-bare rag no-tools live}"
RETRY_FLAGS=""
if [ "${ARCHI_RETRY_ERRORED:-0}" = "1" ]; then RETRY_FLAGS="$RETRY_FLAGS --retry-errored"; fi
if [ "${ARCHI_RETRY_EMPTY:-0}" = "1" ]; then RETRY_FLAGS="$RETRY_FLAGS --retry-empty"; fi

write_wait_for_services_env() {
  cat <<'SH'
wait_for_services_env() {
  local attempt
  for attempt in $(seq 1 240); do
    if [ -f "$SERVICES_ENV_FILE" ]; then
      . "$SERVICES_ENV_FILE"
      if [ -n "${ARCHI_DM_URL:-}" ] && [ -n "${ARCHI_POSTGRES_URL:-}" ]; then
        return 0
      fi
    fi
    sleep 5
  done
  echo "ERROR: timed out waiting for $SERVICES_ENV_FILE with ARCHI_DM_URL and ARCHI_POSTGRES_URL" >&2
  return 1
}
wait_for_services_env

preflight_archi_services() {
  local schema_file
  schema_file=$(mktemp)
  echo "=== Archi service preflight ==="
  curl -fsS -m 15 "$ARCHI_DM_URL/api/catalog/schema" > "$schema_file"
  python3 - "$schema_file" <<'PY'
import json
import sys
path = sys.argv[1]
schema = json.load(open(path))
keys = schema.get("keys") or []
source_types = schema.get("source_types") or []
print(f"catalog_schema: keys={len(keys)} source_types={source_types}")
if "path" not in keys and "file_path" not in keys:
    raise SystemExit(f"catalog schema missing path-like key: keys={keys}")
if not source_types:
    raise SystemExit("catalog schema has no source_types")
PY
  rm -f "$schema_file"

  if [ "$ARCHI_EXPECT_LIVE_TOOLS" = "1" ]; then
    [ -n "${ARCHI_RUCIO_MCP_URL:-}" ] || {
      echo "ERROR: ARCHI_RUCIO_MCP_URL is required for live-tool runs" >&2
      return 2
    }
    echo "rucio_mcp_url: configured"
  fi

  [ -n "${ARCHI_POSTGRES_URL:-}" ] || {
    echo "ERROR: ARCHI_POSTGRES_URL is required for production RAG retrieval" >&2
    return 3
  }
  echo "postgres_url: configured"
}
preflight_archi_services
SH
}

mkdir -p "$HOME/bench_out/$OUT_SUBDIR"

write_sbatch_qa() {
  local cfg=$1
  local concurrency=$QA_CONCURRENCY
  cat > /tmp/run_${JOB_LABEL}_${cfg}.sbatch <<SBATCH
#!/bin/bash
#SBATCH --job-name=archi-${JOB_LABEL}-${cfg}
#SBATCH --output=$HOME/archi-${JOB_LABEL}-${cfg}.%j.out
#SBATCH --time=08:00:00
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --nodes=1

set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null
SERVICES_ENV_FILE="$SERVICES_ENV_FILE"
ARCHI_EXPECT_LIVE_TOOLS=0
$(write_wait_for_services_env)

apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  --bind \$HOME/.archi-bundle-state/bundle/secrets/archi:/secrets:ro \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=\$ARCHI_DM_URL \\
  --env ARCHI_POSTGRES_URL=\$ARCHI_POSTGRES_URL \\
  --env ORCD_OUT_DIR=/bench_out/$OUT_SUBDIR \\
  --env LLM_PROVIDER=openai \\
  --env LLM_MODEL=$MODEL \\
  --env LLM_API_KEY_ENV=OPENAI_API_KEY \\
  --env OPENAI_REASONING_EFFORT=$OPENAI_REASONING_EFFORT \\
  --env OPENAI_USE_RESPONSES_API=$USE_RESPONSES_API \\
  --env LLM_TIMEOUT_S=$LLM_TIMEOUT_S \\
  --env PER_QUESTION_TIMEOUT_S=$PER_QUESTION_TIMEOUT_S \\
  --env CONCURRENCY_OVERRIDE=$concurrency \\
  --env PYTHONPATH=/workspace \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/scripts/paper_benchmark/run_qa.py \\
    --questions $QUESTIONS_PATH \\
    --start $START \\
    --limit $LIMIT \\
    --tool-set ${cfg} \\
    --concurrency $concurrency \\
    $RETRY_FLAGS \\
    --llm-provider openai \\
    --model $MODEL \\
    --api-key-env OPENAI_API_KEY \\
    --reasoning-effort $OPENAI_REASONING_EFFORT \\
    --out /bench_out/$OUT_SUBDIR/results_v3_${cfg}.json
SBATCH
}

write_sbatch_v3() {
  local cfg=$1
  local concurrency=$AGENT_CONCURRENCY
  cat > /tmp/run_${JOB_LABEL}_${cfg}.sbatch <<SBATCH
#!/bin/bash
#SBATCH --job-name=archi-${JOB_LABEL}-${cfg}
#SBATCH --output=$HOME/archi-${JOB_LABEL}-${cfg}.%j.out
#SBATCH --time=08:00:00
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --nodes=1

set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null
SERVICES_ENV_FILE="$SERVICES_ENV_FILE"
ARCHI_EXPECT_LIVE_TOOLS=$([ "$cfg" = "live" ] && echo 1 || echo 0)
$(write_wait_for_services_env)

apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  --bind \$HOME/.archi-bundle-state/bundle/secrets/archi:/secrets:ro \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=\$ARCHI_DM_URL \\
  --env ARCHI_POSTGRES_URL=\$ARCHI_POSTGRES_URL \\
  --env ARCHI_RUCIO_MCP_URL=\${ARCHI_RUCIO_MCP_URL:-} \\
  --env ORCD_OUT_DIR=/bench_out/$OUT_SUBDIR \\
  --env LLM_PROVIDER=openai \\
  --env LLM_MODEL=$MODEL \\
  --env LLM_API_KEY_ENV=OPENAI_API_KEY \\
  --env OPENAI_REASONING_EFFORT=$OPENAI_REASONING_EFFORT \\
  --env OPENAI_USE_RESPONSES_API=$USE_RESPONSES_API \\
  --env LLM_TIMEOUT_S=$LLM_TIMEOUT_S \\
  --env MAX_TOOL_CALLS=$MAX_TOOL_CALLS \\
  --env TOOL_TIMEOUT_S=$TOOL_TIMEOUT_S \\
  --env PER_QUESTION_TIMEOUT_S=$PER_QUESTION_TIMEOUT_S \\
  --env CATALOG_HTTP_TIMEOUT_S=$CATALOG_HTTP_TIMEOUT_S \\
  --env BULK_FETCH_MAX_HASHES=$BULK_FETCH_MAX_HASHES \\
  --env BULK_FETCH_WORKERS=$BULK_FETCH_WORKERS \\
  --env CONCURRENCY_OVERRIDE=$concurrency \\
  --env PYTHONPATH=/workspace \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/scripts/paper_benchmark/run_agent.py \\
    --questions $QUESTIONS_PATH \\
    --start $START \\
    --limit $LIMIT \\
    --tool-set ${cfg} \\
    --concurrency $concurrency \\
    --max-tool-calls $MAX_TOOL_CALLS \\
    $RETRY_FLAGS \\
    --llm-provider openai \\
    --model $MODEL \\
    --api-key-env OPENAI_API_KEY \\
    --reasoning-effort $OPENAI_REASONING_EFFORT \\
    --out /bench_out/$OUT_SUBDIR/results_v3_${cfg}.json
SBATCH
}

prev="$INITIAL_DEPENDENCY"
dep_type="$INITIAL_DEPENDENCY_TYPE"
first_cfg=""
for cfg in $CONFIGS; do
  case "$cfg" in
    bare|rag)
      write_sbatch_qa "$cfg"
      ;;
    no-tools|live)
      write_sbatch_v3 "$cfg"
      ;;
    *)
      echo "ERROR: unknown config '$cfg' in ARCHI_CONFIGS='$CONFIGS'" >&2
      exit 5
      ;;
  esac

  dep=()
  if [ -n "$prev" ]; then
    dep=(--dependency=${dep_type}:$prev)
  fi
  jid=$(sbatch --parsable "${dep[@]}" /tmp/run_${JOB_LABEL}_${cfg}.sbatch)
  if [ -n "$prev" ]; then
    echo "$cfg: $jid  (after $prev)"
  else
    echo "$cfg: $jid"
  fi
  [ -n "$first_cfg" ] || first_cfg="$cfg"
  prev="$jid"
  dep_type="afterany"
done

echo
echo "=== first sbatch ($first_cfg) preview ==="
head -35 /tmp/run_${JOB_LABEL}_${first_cfg}.sbatch
echo
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M %R"
