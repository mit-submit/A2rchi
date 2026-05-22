#!/bin/bash
# Inner script run on orcd-login: submit the four GPT-5.5 270Q configs.
# No vLLM/GPU job is needed; rag/no-tools/live still use archi-services.
set -euo pipefail

[ -f "$HOME/archi-services.env" ] || { echo "ERROR: missing $HOME/archi-services.env" >&2; exit 2; }
. "$HOME/archi-services.env"

[ -n "${ARCHI_DM_URL:-}" ] || { echo "ERROR: ARCHI_DM_URL is unset after sourcing archi-services.env" >&2; exit 3; }

SECRET_FILE="$HOME/.archi-bundle-state/bundle/secrets/archi/openai_api_key.txt"
[ -s "$SECRET_FILE" ] || { echo "ERROR: missing $SECRET_FILE; run .scratch/write_openai_secret_to_orcd.sh first" >&2; exit 4; }

MODEL="${ARCHI_OPENAI_MODEL:-gpt-5.5-2026-04-23}"
LIMIT="${ARCHI_LIMIT:-270}"
START="${ARCHI_START:-0}"
QUESTIONS_PATH="${ARCHI_QUESTIONS_PATH:-/workspace/configs/submit75/curated_questions_270.json}"
OUT_SUBDIR="${ARCHI_OUT_SUBDIR:-run_270q_gpt55_openai}"
QA_CONCURRENCY="${ARCHI_QA_CONCURRENCY:-8}"
AGENT_CONCURRENCY="${ARCHI_AGENT_CONCURRENCY:-4}"
MAX_TOOL_CALLS="${ARCHI_MAX_TOOL_CALLS:-30}"
TOOL_TIMEOUT_S="${ARCHI_TOOL_TIMEOUT_S:-30}"
PER_QUESTION_TIMEOUT_S="${ARCHI_PER_QUESTION_TIMEOUT_S:-600}"
CATALOG_HTTP_TIMEOUT_S="${ARCHI_CATALOG_HTTP_TIMEOUT_S:-20}"
BULK_FETCH_MAX_HASHES="${ARCHI_BULK_FETCH_MAX_HASHES:-8}"
BULK_FETCH_WORKERS="${ARCHI_BULK_FETCH_WORKERS:-4}"
OPENAI_REASONING_EFFORT="${OPENAI_REASONING_EFFORT:-high}"
USE_RESPONSES_API="${OPENAI_USE_RESPONSES_API:-1}"
CONFIGS="${ARCHI_CONFIGS:-bare rag no-tools live}"

mkdir -p "$HOME/bench_out/$OUT_SUBDIR"

write_sbatch_qa() {
  local cfg=$1
  local concurrency=$QA_CONCURRENCY
  cat > /tmp/run_gpt55_${cfg}.sbatch <<SBATCH
#!/bin/bash
#SBATCH --job-name=archi-gpt55-${cfg}
#SBATCH --output=$HOME/archi-gpt55-${cfg}.%j.out
#SBATCH --time=08:00:00
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --nodes=1

set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null
. \$HOME/archi-services.env

apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  --bind \$HOME/.archi-bundle-state/bundle/secrets/archi:/secrets:ro \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=\$ARCHI_DM_URL \\
  --env ORCD_OUT_DIR=/bench_out/$OUT_SUBDIR \\
  --env LLM_PROVIDER=openai \\
  --env LLM_MODEL=$MODEL \\
  --env LLM_API_KEY_ENV=OPENAI_API_KEY \\
  --env OPENAI_REASONING_EFFORT=$OPENAI_REASONING_EFFORT \\
  --env OPENAI_USE_RESPONSES_API=$USE_RESPONSES_API \\
  --env PER_QUESTION_TIMEOUT_S=$PER_QUESTION_TIMEOUT_S \\
  --env CONCURRENCY_OVERRIDE=$concurrency \\
  --env PYTHONPATH=/workspace \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/.scratch/run_260q_orcd_qa.py \\
    --questions $QUESTIONS_PATH \\
    --start $START \\
    --limit $LIMIT \\
    --tool-set ${cfg} \\
    --concurrency $concurrency \\
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
  cat > /tmp/run_gpt55_${cfg}.sbatch <<SBATCH
#!/bin/bash
#SBATCH --job-name=archi-gpt55-${cfg}
#SBATCH --output=$HOME/archi-gpt55-${cfg}.%j.out
#SBATCH --time=08:00:00
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --nodes=1

set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null
. \$HOME/archi-services.env

apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  --bind \$HOME/.archi-bundle-state/bundle/secrets/archi:/secrets:ro \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=\$ARCHI_DM_URL \\
  --env ARCHI_RUCIO_MCP_URL=\${ARCHI_RUCIO_MCP_URL:-} \\
  --env ORCD_OUT_DIR=/bench_out/$OUT_SUBDIR \\
  --env LLM_PROVIDER=openai \\
  --env LLM_MODEL=$MODEL \\
  --env LLM_API_KEY_ENV=OPENAI_API_KEY \\
  --env OPENAI_REASONING_EFFORT=$OPENAI_REASONING_EFFORT \\
  --env OPENAI_USE_RESPONSES_API=$USE_RESPONSES_API \\
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
  python3 /workspace/.scratch/run_260q_orcd_v3.py \\
    --questions $QUESTIONS_PATH \\
    --start $START \\
    --limit $LIMIT \\
    --tool-set ${cfg} \\
    --concurrency $concurrency \\
    --max-tool-calls $MAX_TOOL_CALLS \\
    --llm-provider openai \\
    --model $MODEL \\
    --api-key-env OPENAI_API_KEY \\
    --reasoning-effort $OPENAI_REASONING_EFFORT \\
    --out /bench_out/$OUT_SUBDIR/results_v3_${cfg}.json
SBATCH
}

prev=""
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
    dep=(--dependency=afterany:$prev)
  fi
  jid=$(sbatch --parsable "${dep[@]}" /tmp/run_gpt55_${cfg}.sbatch)
  if [ -n "$prev" ]; then
    echo "$cfg: $jid  (after $prev)"
  else
    echo "$cfg: $jid"
    first_cfg="$cfg"
  fi
  prev="$jid"
done

echo
echo "=== first sbatch ($first_cfg) preview ==="
head -35 /tmp/run_gpt55_${first_cfg}.sbatch
echo
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M %R"
