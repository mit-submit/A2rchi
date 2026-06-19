#!/bin/bash
# Inner script run on orcd-login: writes 4 sbatch files + submits them.
# Reads LIMIT/CONCURRENCY/etc from ARCHI_* env vars set by the caller.
set -euo pipefail
umask 077

SERVICES_ENV_FILE="${ARCHI_SERVICES_ENV_FILE:-$HOME/archi-services.env}"
OKG_SERVICES_ENV_FILE="${ARCHI_OKG_SERVICES_ENV_FILE:-$HOME/cms-okg-services.env}"
VLLM_ENV_FILE="${ARCHI_VLLM_ENV_FILE:-$HOME/archi-vllm.env}"
[ -f "$VLLM_ENV_FILE" ] || { echo "ERROR: missing $VLLM_ENV_FILE" >&2; exit 3; }
BENCHMARK_TIER="${BENCHMARK_TIER:-orcd-vllm-corrected}"
KNOWLEDGE_BACKEND="${ARCHI_KNOWLEDGE_BACKEND:-}"
if [ -z "$KNOWLEDGE_BACKEND" ]; then
  if [ "$BENCHMARK_TIER" = "orcd-vllm-okg" ]; then
    KNOWLEDGE_BACKEND="okg"
  else
    KNOWLEDGE_BACKEND="data_manager"
  fi
fi

if [ "$KNOWLEDGE_BACKEND" = "okg" ]; then
  [ -f "$OKG_SERVICES_ENV_FILE" ] || { echo "ERROR: missing $OKG_SERVICES_ENV_FILE" >&2; exit 2; }
  . "$OKG_SERVICES_ENV_FILE"
  # OKG supplies the corpus read side. The standard A2rchi services env still
  # carries live-tool endpoints such as ARCHI_RUCIO_MCP_URL for full-agent runs.
  if [ -f "$SERVICES_ENV_FILE" ]; then
    . "$SERVICES_ENV_FILE"
  fi
else
  [ -f "$SERVICES_ENV_FILE" ] || { echo "ERROR: missing $SERVICES_ENV_FILE" >&2; exit 2; }
  . "$SERVICES_ENV_FILE"
fi
. "$VLLM_ENV_FILE"

if [ "$KNOWLEDGE_BACKEND" = "okg" ]; then
  [ -n "${OKG_DSN:-}" ] || { echo "ERROR: OKG_DSN is unset after sourcing $OKG_SERVICES_ENV_FILE" >&2; exit 4; }
else
  [ -n "${ARCHI_DM_URL:-}" ] || { echo "ERROR: ARCHI_DM_URL is unset after sourcing $SERVICES_ENV_FILE" >&2; exit 4; }
  [ -n "${ARCHI_POSTGRES_URL:-}" ] || { echo "ERROR: ARCHI_POSTGRES_URL is unset after sourcing $SERVICES_ENV_FILE" >&2; exit 5; }
fi
[ -n "${VLLM_URL:-}" ] || { echo "ERROR: VLLM_URL is unset after sourcing $VLLM_ENV_FILE" >&2; exit 6; }
[ -n "${VLLM_MODEL:-}" ] || { echo "ERROR: VLLM_MODEL is unset after sourcing $VLLM_ENV_FILE" >&2; exit 7; }
ARCHI_DM_URL="${ARCHI_DM_URL:-}"
ARCHI_POSTGRES_URL="${ARCHI_POSTGRES_URL:-}"
ARCHI_RUCIO_MCP_URL="${ARCHI_RUCIO_MCP_URL:-}"

if [ "$KNOWLEDGE_BACKEND" = "okg" ]; then
  LIMIT="${ARCHI_LIMIT:-63}"
  QUESTIONS_PATH="${ARCHI_QUESTIONS_PATH:-/workspace/configs/submit75/grading_questions_current_63_from_270.json}"
else
  LIMIT="${ARCHI_LIMIT:-270}"
  QUESTIONS_PATH="${ARCHI_QUESTIONS_PATH:-/workspace/configs/submit75/curated_questions_270.json}"
fi
CONCURRENCY="${ARCHI_CONCURRENCY:-32}"
QA_CONCURRENCY="${ARCHI_QA_CONCURRENCY:-$CONCURRENCY}"
AGENT_CONCURRENCY="${ARCHI_AGENT_CONCURRENCY:-$CONCURRENCY}"
MAX_TOOL_CALLS="${ARCHI_MAX_TOOL_CALLS:-30}"
TOOL_TIMEOUT_S="${ARCHI_TOOL_TIMEOUT_S:-30}"
PER_QUESTION_TIMEOUT_S="${ARCHI_PER_QUESTION_TIMEOUT_S:-600}"
LLM_TIMEOUT_S="${ARCHI_LLM_TIMEOUT_S:-200}"
CATALOG_HTTP_TIMEOUT_S="${ARCHI_CATALOG_HTTP_TIMEOUT_S:-20}"
BULK_FETCH_MAX_HASHES="${ARCHI_BULK_FETCH_MAX_HASHES:-8}"
BULK_FETCH_WORKERS="${ARCHI_BULK_FETCH_WORKERS:-4}"
OUTPUT_PREVIEW_CHARS="${ARCHI_OUTPUT_PREVIEW_CHARS:-2000}"
TOOL_MESSAGE_MAX_CHARS="${ARCHI_TOOL_MESSAGE_MAX_CHARS:-$OUTPUT_PREVIEW_CHARS}"
VLLM_ENABLE_THINKING="${VLLM_ENABLE_THINKING:-1}"
VLLM_TENSOR_PARALLEL="${VLLM_TENSOR_PARALLEL:-}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-}"
VLLM_ENABLE_EXPERT_PARALLEL="${VLLM_ENABLE_EXPERT_PARALLEL:-}"
VLLM_MTP_TOKENS="${VLLM_MTP_TOKENS:-}"
VLLM_TOOL_CALL_PARSER="${VLLM_TOOL_CALL_PARSER:-}"
VLLM_REASONING_PARSER="${VLLM_REASONING_PARSER:-}"
VLLM_DTYPE="${VLLM_DTYPE:-}"
VLLM_QUANTIZATION="${VLLM_QUANTIZATION:-}"
CONTRACT_PATH="${ARCHI_CONTRACT_PATH:-/workspace/configs/submit75/orcd_vllm_corrected_contract.json}"
OUT_SUBDIR="${ARCHI_OUT_SUBDIR:-run_270q_orcd_v3}"
OUT_CONTAINER="/bench_out/$OUT_SUBDIR"
OUT_HOST="$HOME/bench_out/$OUT_SUBDIR"
DEPENDENCY_MODE="${ARCHI_DEPENDENCY_MODE:-chain}"
PREFLIGHT_ONLY="${ARCHI_PREFLIGHT_ONLY:-0}"
SBATCH_TIME="${ARCHI_SBATCH_TIME:-08:00:00}"
SBATCH_CPUS="${ARCHI_SBATCH_CPUS:-16}"
SBATCH_MEM="${ARCHI_SBATCH_MEM:-64G}"
SBATCH_NODE_DIRECTIVES=""
if [ -n "${ARCHI_SBATCH_NODELIST:-}" ]; then
  SBATCH_NODE_DIRECTIVES+="#SBATCH --nodelist=${ARCHI_SBATCH_NODELIST}"$'\n'
fi
if [ -n "${ARCHI_SBATCH_EXCLUDE:-}" ]; then
  SBATCH_NODE_DIRECTIVES+="#SBATCH --exclude=${ARCHI_SBATCH_EXCLUDE}"$'\n'
fi
if [ "$PREFLIGHT_ONLY" = "1" ]; then
  SBATCH_TIME="${ARCHI_PREFLIGHT_TIME:-00:10:00}"
  SBATCH_CPUS="${ARCHI_PREFLIGHT_CPUS:-2}"
  SBATCH_MEM="${ARCHI_PREFLIGHT_MEM:-4G}"
fi
if [ "$KNOWLEDGE_BACKEND" = "okg" ]; then
  CONFIGS="${ARCHI_CONFIGS:-no-tools}"
else
  CONFIGS="${ARCHI_CONFIGS:-bare rag no-tools live}"
fi
OKG_DEPLOYMENT="${OKG_DEPLOYMENT:-cms}"
OKG_BRANCH="${OKG_BRANCH:-default}"
OKG_GENERATION_ID="${OKG_GENERATION_ID:-}"
OKG_COMPAT_GENERATION_ID="${OKG_COMPAT_GENERATION_ID:-}"
if [ "$KNOWLEDGE_BACKEND" = "okg" ]; then
  OKG_READ_SURFACE="${ARCHI_OKG_READ_SURFACE:-mcp_search}"
else
  OKG_READ_SURFACE="${ARCHI_OKG_READ_SURFACE:-${OKG_READ_SURFACE:-mcp_search}}"
fi
OKG_RETRIEVAL_METHOD="${OKG_RETRIEVAL_METHOD:-lexical}"
OKG_TOP_K="${OKG_TOP_K:-8}"
OKG_PROBE_QUERY="${OKG_PROBE_QUERY:-Rucio transfer failure}"
OKG_MCP_TOOL_ALLOWLIST="${OKG_MCP_TOOL_ALLOWLIST:-}"
ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES="${ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES:-0}"
ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS="${ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS:-1}"
PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"
OKG_PYTHONPATH="${OKG_PYTHONPATH:-${CMS_OKG_FRAMEWORK_DIR:+$CMS_OKG_FRAMEWORK_DIR/src}}"
OKG_FRAMEWORK_BIND="${CMS_OKG_FRAMEWORK_DIR:-$HOME/okg}"
OKG_MCP_DEPLOYMENT_NAME="${OKG_MCP_DEPLOYMENT_NAME:-${CMS_OKG_DEPLOYMENTS_DIR:-$HOME/okg-deployments}/cms}"
OKG_MCP_COMMAND="${OKG_MCP_COMMAND:-$HOME/.local/bin/uv}"
OKG_MCP_CWD="${OKG_MCP_CWD:-$OKG_FRAMEWORK_BIND}"
if [ "$KNOWLEDGE_BACKEND" = "okg" ]; then
  [ -d "$OKG_FRAMEWORK_BIND/src/okg" ] || { echo "ERROR: OKG framework not found at $OKG_FRAMEWORK_BIND" >&2; exit 11; }
  if [ "$OKG_READ_SURFACE" = "mcp_search" ]; then
    [ -x "$OKG_MCP_COMMAND" ] || command -v "$OKG_MCP_COMMAND" >/dev/null 2>&1 || { echo "ERROR: OKG MCP command not executable/found: $OKG_MCP_COMMAND" >&2; exit 13; }
    [ -d "$OKG_MCP_CWD" ] || { echo "ERROR: OKG_MCP_CWD not found at $OKG_MCP_CWD" >&2; exit 14; }
  fi
  if [ "$OKG_READ_SURFACE" = "direct_sql" ]; then
    [ -n "$OKG_GENERATION_ID" ] || [ -n "$OKG_COMPAT_GENERATION_ID" ] || { echo "ERROR: OKG_GENERATION_ID or OKG_COMPAT_GENERATION_ID is required when OKG_READ_SURFACE=direct_sql" >&2; exit 12; }
  fi
fi
RETRY_FLAGS=""
if [ "${ARCHI_RETRY_ERRORED:-0}" = "1" ]; then RETRY_FLAGS="$RETRY_FLAGS --retry-errored"; fi
if [ "${ARCHI_RETRY_EMPTY:-0}" = "1" ]; then RETRY_FLAGS="$RETRY_FLAGS --retry-empty"; fi
mkdir -p "$OUT_HOST"

write_sbatch_qa() {
  local jobname=$1
  local concurrency=$QA_CONCURRENCY
  local require_sources=0
  if [ "$jobname" = "rag" ]; then require_sources=1; fi
  cat > "/tmp/run_${OUT_SUBDIR}_${jobname}.sbatch" <<SBATCH
#!/bin/bash
#SBATCH --job-name=archi-bench-${jobname}
#SBATCH --output=$HOME/archi-bench-${OUT_SUBDIR}-${jobname}.%j.out
#SBATCH --time=$SBATCH_TIME
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=$SBATCH_CPUS
#SBATCH --mem=$SBATCH_MEM
#SBATCH --nodes=1
${SBATCH_NODE_DIRECTIVES}

set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null
OKG_BIND_ARGS=()
if [ "$KNOWLEDGE_BACKEND" = "okg" ]; then
  OKG_BIND_ARGS+=(--bind "$OKG_FRAMEWORK_BIND:$OKG_FRAMEWORK_BIND")
fi
mkdir -p \$HOME/bench_out/$OUT_SUBDIR

apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  "\${OKG_BIND_ARGS[@]}" \\
  --bind \$HOME/.archi-bundle-state/bundle/secrets/archi:/secrets:ro \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=$ARCHI_DM_URL \\
  --env ARCHI_POSTGRES_URL=$ARCHI_POSTGRES_URL \\
  --env ARCHI_KNOWLEDGE_BACKEND=$KNOWLEDGE_BACKEND \\
  --env ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES=$ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES \\
  --env ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS=$ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS \\
  --env OKG_MCP_TOOL_ALLOWLIST="$OKG_MCP_TOOL_ALLOWLIST" \\
  --env PYTHONUNBUFFERED=$PYTHONUNBUFFERED \\
  --env OKG_DSN="${OKG_DSN:-}" \\
  --env OKG_DSN_ENV=OKG_DSN \\
  --env OKG_DEPLOYMENT=$OKG_DEPLOYMENT \\
  --env OKG_BRANCH=$OKG_BRANCH \\
  --env OKG_GENERATION_ID=$OKG_GENERATION_ID \\
  --env OKG_COMPAT_GENERATION_ID=$OKG_COMPAT_GENERATION_ID \\
  --env OKG_READ_SURFACE=$OKG_READ_SURFACE \\
  --env OKG_RETRIEVAL_METHOD=$OKG_RETRIEVAL_METHOD \\
  --env OKG_TOP_K=$OKG_TOP_K \\
  --env OKG_PROBE_QUERY="$OKG_PROBE_QUERY" \\
  --env OKG_PYTHONPATH="$OKG_PYTHONPATH" \\
  --env CMS_OKG_FRAMEWORK_DIR="${CMS_OKG_FRAMEWORK_DIR:-}" \\
  --env OKG_MCP_DEPLOYMENT_NAME="$OKG_MCP_DEPLOYMENT_NAME" \\
  --env OKG_MCP_COMMAND="$OKG_MCP_COMMAND" \\
  --env OKG_MCP_CWD="$OKG_MCP_CWD" \\
  --env VLLM_URL=$VLLM_URL \\
  --env VLLM_MODEL=$VLLM_MODEL \\
  --env VLLM_ENABLE_THINKING=$VLLM_ENABLE_THINKING \\
  --env VLLM_TENSOR_PARALLEL=$VLLM_TENSOR_PARALLEL \\
  --env VLLM_MAX_MODEL_LEN=$VLLM_MAX_MODEL_LEN \\
  --env VLLM_ENABLE_EXPERT_PARALLEL=$VLLM_ENABLE_EXPERT_PARALLEL \\
  --env VLLM_MTP_TOKENS=$VLLM_MTP_TOKENS \\
  --env VLLM_TOOL_CALL_PARSER=$VLLM_TOOL_CALL_PARSER \\
  --env VLLM_REASONING_PARSER=$VLLM_REASONING_PARSER \\
  --env VLLM_DTYPE=$VLLM_DTYPE \\
  --env VLLM_QUANTIZATION=$VLLM_QUANTIZATION \\
  --env BENCHMARK_TIER=$BENCHMARK_TIER \\
  --env ORCD_OUT_DIR=$OUT_CONTAINER \\
  --env PER_QUESTION_TIMEOUT_S=$PER_QUESTION_TIMEOUT_S \\
  --env LLM_TIMEOUT_S=$LLM_TIMEOUT_S \\
  --env CONCURRENCY_OVERRIDE=$concurrency \\
  --env PYTHONPATH=/workspace:$OKG_PYTHONPATH \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/scripts/paper_benchmark/preflight.py \\
    --tool-set ${jobname} \\
    --questions $QUESTIONS_PATH \\
    --contract $CONTRACT_PATH \\
    --manifest-out $OUT_CONTAINER/preflight_${jobname}.json \\
    --require-service-health

if [ "$PREFLIGHT_ONLY" = "1" ]; then
  echo "ARCHI_PREFLIGHT_ONLY=1; stopping after preflight"
  exit 0
fi

apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  "\${OKG_BIND_ARGS[@]}" \\
  --bind \$HOME/.archi-bundle-state/bundle/secrets/archi:/secrets:ro \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=$ARCHI_DM_URL \\
  --env ARCHI_POSTGRES_URL=$ARCHI_POSTGRES_URL \\
  --env ARCHI_KNOWLEDGE_BACKEND=$KNOWLEDGE_BACKEND \\
  --env ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES=$ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES \\
  --env ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS=$ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS \\
  --env OKG_MCP_TOOL_ALLOWLIST="$OKG_MCP_TOOL_ALLOWLIST" \\
  --env PYTHONUNBUFFERED=$PYTHONUNBUFFERED \\
  --env OKG_DSN="${OKG_DSN:-}" \\
  --env OKG_DSN_ENV=OKG_DSN \\
  --env OKG_DEPLOYMENT=$OKG_DEPLOYMENT \\
  --env OKG_BRANCH=$OKG_BRANCH \\
  --env OKG_GENERATION_ID=$OKG_GENERATION_ID \\
  --env OKG_COMPAT_GENERATION_ID=$OKG_COMPAT_GENERATION_ID \\
  --env OKG_READ_SURFACE=$OKG_READ_SURFACE \\
  --env OKG_RETRIEVAL_METHOD=$OKG_RETRIEVAL_METHOD \\
  --env OKG_TOP_K=$OKG_TOP_K \\
  --env OKG_PROBE_QUERY="$OKG_PROBE_QUERY" \\
  --env OKG_PYTHONPATH="$OKG_PYTHONPATH" \\
  --env CMS_OKG_FRAMEWORK_DIR="${CMS_OKG_FRAMEWORK_DIR:-}" \\
  --env OKG_MCP_DEPLOYMENT_NAME="$OKG_MCP_DEPLOYMENT_NAME" \\
  --env OKG_MCP_COMMAND="$OKG_MCP_COMMAND" \\
  --env OKG_MCP_CWD="$OKG_MCP_CWD" \\
  --env VLLM_URL=$VLLM_URL \\
  --env VLLM_MODEL=$VLLM_MODEL \\
  --env VLLM_ENABLE_THINKING=$VLLM_ENABLE_THINKING \\
  --env VLLM_TENSOR_PARALLEL=$VLLM_TENSOR_PARALLEL \\
  --env VLLM_MAX_MODEL_LEN=$VLLM_MAX_MODEL_LEN \\
  --env VLLM_ENABLE_EXPERT_PARALLEL=$VLLM_ENABLE_EXPERT_PARALLEL \\
  --env VLLM_MTP_TOKENS=$VLLM_MTP_TOKENS \\
  --env VLLM_TOOL_CALL_PARSER=$VLLM_TOOL_CALL_PARSER \\
  --env VLLM_REASONING_PARSER=$VLLM_REASONING_PARSER \\
  --env VLLM_DTYPE=$VLLM_DTYPE \\
  --env VLLM_QUANTIZATION=$VLLM_QUANTIZATION \\
  --env BENCHMARK_TIER=$BENCHMARK_TIER \\
  --env ORCD_OUT_DIR=$OUT_CONTAINER \\
  --env PER_QUESTION_TIMEOUT_S=$PER_QUESTION_TIMEOUT_S \\
  --env LLM_TIMEOUT_S=$LLM_TIMEOUT_S \\
  --env CONCURRENCY_OVERRIDE=$concurrency \\
  --env PYTHONPATH=/workspace:$OKG_PYTHONPATH \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/scripts/paper_benchmark/run_qa.py \\
    --questions $QUESTIONS_PATH \\
    --limit $LIMIT \\
    --tool-set ${jobname} \\
    --concurrency $concurrency \\
    $RETRY_FLAGS \\
    --out $OUT_CONTAINER/results_v3_${jobname}.json

postflight_args=()
if [ "$require_sources" = "1" ]; then postflight_args+=(--require-sources); fi
apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/scripts/paper_benchmark/postflight.py \\
    $OUT_CONTAINER/results_v3_${jobname}.json \\
    --expected-count $LIMIT \\
    --expected-tier $BENCHMARK_TIER \\
    --max-errors 0 \\
    "\${postflight_args[@]}" \\
    --manifest-out $OUT_CONTAINER/postflight_${jobname}.json
SBATCH
}

write_sbatch_v3() {
  local jobname=$1
  local concurrency=$AGENT_CONCURRENCY
  cat > "/tmp/run_${OUT_SUBDIR}_${jobname}.sbatch" <<SBATCH
#!/bin/bash
#SBATCH --job-name=archi-bench-${jobname}
#SBATCH --output=$HOME/archi-bench-${OUT_SUBDIR}-${jobname}.%j.out
#SBATCH --time=$SBATCH_TIME
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=$SBATCH_CPUS
#SBATCH --mem=$SBATCH_MEM
#SBATCH --nodes=1
${SBATCH_NODE_DIRECTIVES}

set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null
OKG_BIND_ARGS=()
if [ "$KNOWLEDGE_BACKEND" = "okg" ]; then
  OKG_BIND_ARGS+=(--bind "$OKG_FRAMEWORK_BIND:$OKG_FRAMEWORK_BIND")
fi
mkdir -p \$HOME/bench_out/$OUT_SUBDIR

apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  "\${OKG_BIND_ARGS[@]}" \\
  --bind \$HOME/.archi-bundle-state/bundle/secrets/archi:/secrets:ro \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=$ARCHI_DM_URL \\
  --env ARCHI_POSTGRES_URL=$ARCHI_POSTGRES_URL \\
  --env ARCHI_RUCIO_MCP_URL=${ARCHI_RUCIO_MCP_URL:-} \\
  --env ARCHI_KNOWLEDGE_BACKEND=$KNOWLEDGE_BACKEND \\
  --env ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES=$ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES \\
  --env ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS=$ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS \\
  --env OKG_MCP_TOOL_ALLOWLIST="$OKG_MCP_TOOL_ALLOWLIST" \\
  --env PYTHONUNBUFFERED=$PYTHONUNBUFFERED \\
  --env OKG_DSN="${OKG_DSN:-}" \\
  --env OKG_DSN_ENV=OKG_DSN \\
  --env OKG_DEPLOYMENT=$OKG_DEPLOYMENT \\
  --env OKG_BRANCH=$OKG_BRANCH \\
  --env OKG_GENERATION_ID=$OKG_GENERATION_ID \\
  --env OKG_COMPAT_GENERATION_ID=$OKG_COMPAT_GENERATION_ID \\
  --env OKG_READ_SURFACE=$OKG_READ_SURFACE \\
  --env OKG_RETRIEVAL_METHOD=$OKG_RETRIEVAL_METHOD \\
  --env OKG_TOP_K=$OKG_TOP_K \\
  --env OKG_PROBE_QUERY="$OKG_PROBE_QUERY" \\
  --env OKG_PYTHONPATH="$OKG_PYTHONPATH" \\
  --env CMS_OKG_FRAMEWORK_DIR="${CMS_OKG_FRAMEWORK_DIR:-}" \\
  --env OKG_MCP_DEPLOYMENT_NAME="$OKG_MCP_DEPLOYMENT_NAME" \\
  --env OKG_MCP_COMMAND="$OKG_MCP_COMMAND" \\
  --env OKG_MCP_CWD="$OKG_MCP_CWD" \\
  --env VLLM_URL=$VLLM_URL \\
  --env VLLM_MODEL=$VLLM_MODEL \\
  --env VLLM_ENABLE_THINKING=$VLLM_ENABLE_THINKING \\
  --env VLLM_TENSOR_PARALLEL=$VLLM_TENSOR_PARALLEL \\
  --env VLLM_MAX_MODEL_LEN=$VLLM_MAX_MODEL_LEN \\
  --env VLLM_ENABLE_EXPERT_PARALLEL=$VLLM_ENABLE_EXPERT_PARALLEL \\
  --env VLLM_MTP_TOKENS=$VLLM_MTP_TOKENS \\
  --env VLLM_TOOL_CALL_PARSER=$VLLM_TOOL_CALL_PARSER \\
  --env VLLM_REASONING_PARSER=$VLLM_REASONING_PARSER \\
  --env VLLM_DTYPE=$VLLM_DTYPE \\
  --env VLLM_QUANTIZATION=$VLLM_QUANTIZATION \\
  --env BENCHMARK_TIER=$BENCHMARK_TIER \\
  --env ORCD_OUT_DIR=$OUT_CONTAINER \\
  --env MAX_TOOL_CALLS=$MAX_TOOL_CALLS \\
  --env TOOL_TIMEOUT_S=$TOOL_TIMEOUT_S \\
  --env PER_QUESTION_TIMEOUT_S=$PER_QUESTION_TIMEOUT_S \\
  --env LLM_TIMEOUT_S=$LLM_TIMEOUT_S \\
  --env CATALOG_HTTP_TIMEOUT_S=$CATALOG_HTTP_TIMEOUT_S \\
  --env BULK_FETCH_MAX_HASHES=$BULK_FETCH_MAX_HASHES \\
  --env BULK_FETCH_WORKERS=$BULK_FETCH_WORKERS \\
  --env OUTPUT_PREVIEW_CHARS=$OUTPUT_PREVIEW_CHARS \\
  --env TOOL_MESSAGE_MAX_CHARS=$TOOL_MESSAGE_MAX_CHARS \\
  --env CONCURRENCY_OVERRIDE=$concurrency \\
  --env PYTHONPATH=/workspace:$OKG_PYTHONPATH \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/scripts/paper_benchmark/preflight.py \\
    --tool-set ${jobname} \\
    --questions $QUESTIONS_PATH \\
    --contract $CONTRACT_PATH \\
    --manifest-out $OUT_CONTAINER/preflight_${jobname}.json \\
    --require-service-health

if [ "$PREFLIGHT_ONLY" = "1" ]; then
  echo "ARCHI_PREFLIGHT_ONLY=1; stopping after preflight"
  exit 0
fi

apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  "\${OKG_BIND_ARGS[@]}" \\
  --bind \$HOME/.archi-bundle-state/bundle/secrets/archi:/secrets:ro \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=$ARCHI_DM_URL \\
  --env ARCHI_POSTGRES_URL=$ARCHI_POSTGRES_URL \\
  --env ARCHI_RUCIO_MCP_URL=${ARCHI_RUCIO_MCP_URL:-} \\
  --env ARCHI_KNOWLEDGE_BACKEND=$KNOWLEDGE_BACKEND \\
  --env ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES=$ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES \\
  --env ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS=$ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS \\
  --env OKG_MCP_TOOL_ALLOWLIST="$OKG_MCP_TOOL_ALLOWLIST" \\
  --env PYTHONUNBUFFERED=$PYTHONUNBUFFERED \\
  --env OKG_DSN="${OKG_DSN:-}" \\
  --env OKG_DSN_ENV=OKG_DSN \\
  --env OKG_DEPLOYMENT=$OKG_DEPLOYMENT \\
  --env OKG_BRANCH=$OKG_BRANCH \\
  --env OKG_GENERATION_ID=$OKG_GENERATION_ID \\
  --env OKG_COMPAT_GENERATION_ID=$OKG_COMPAT_GENERATION_ID \\
  --env OKG_READ_SURFACE=$OKG_READ_SURFACE \\
  --env OKG_RETRIEVAL_METHOD=$OKG_RETRIEVAL_METHOD \\
  --env OKG_TOP_K=$OKG_TOP_K \\
  --env OKG_PROBE_QUERY="$OKG_PROBE_QUERY" \\
  --env OKG_PYTHONPATH="$OKG_PYTHONPATH" \\
  --env CMS_OKG_FRAMEWORK_DIR="${CMS_OKG_FRAMEWORK_DIR:-}" \\
  --env OKG_MCP_DEPLOYMENT_NAME="$OKG_MCP_DEPLOYMENT_NAME" \\
  --env OKG_MCP_COMMAND="$OKG_MCP_COMMAND" \\
  --env OKG_MCP_CWD="$OKG_MCP_CWD" \\
  --env VLLM_URL=$VLLM_URL \\
  --env VLLM_MODEL=$VLLM_MODEL \\
  --env VLLM_ENABLE_THINKING=$VLLM_ENABLE_THINKING \\
  --env VLLM_TENSOR_PARALLEL=$VLLM_TENSOR_PARALLEL \\
  --env VLLM_MAX_MODEL_LEN=$VLLM_MAX_MODEL_LEN \\
  --env VLLM_ENABLE_EXPERT_PARALLEL=$VLLM_ENABLE_EXPERT_PARALLEL \\
  --env VLLM_MTP_TOKENS=$VLLM_MTP_TOKENS \\
  --env VLLM_TOOL_CALL_PARSER=$VLLM_TOOL_CALL_PARSER \\
  --env VLLM_REASONING_PARSER=$VLLM_REASONING_PARSER \\
  --env VLLM_DTYPE=$VLLM_DTYPE \\
  --env VLLM_QUANTIZATION=$VLLM_QUANTIZATION \\
  --env BENCHMARK_TIER=$BENCHMARK_TIER \\
  --env ORCD_OUT_DIR=$OUT_CONTAINER \\
  --env MAX_TOOL_CALLS=$MAX_TOOL_CALLS \\
  --env TOOL_TIMEOUT_S=$TOOL_TIMEOUT_S \\
  --env PER_QUESTION_TIMEOUT_S=$PER_QUESTION_TIMEOUT_S \\
  --env LLM_TIMEOUT_S=$LLM_TIMEOUT_S \\
  --env CATALOG_HTTP_TIMEOUT_S=$CATALOG_HTTP_TIMEOUT_S \\
  --env BULK_FETCH_MAX_HASHES=$BULK_FETCH_MAX_HASHES \\
  --env BULK_FETCH_WORKERS=$BULK_FETCH_WORKERS \\
  --env OUTPUT_PREVIEW_CHARS=$OUTPUT_PREVIEW_CHARS \\
  --env TOOL_MESSAGE_MAX_CHARS=$TOOL_MESSAGE_MAX_CHARS \\
  --env CONCURRENCY_OVERRIDE=$concurrency \\
  --env PYTHONPATH=/workspace:$OKG_PYTHONPATH \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/scripts/paper_benchmark/run_agent.py \\
    --questions $QUESTIONS_PATH \\
    --limit $LIMIT \\
    --tool-set ${jobname} \\
    --concurrency $concurrency \\
    --max-tool-calls $MAX_TOOL_CALLS \\
    $RETRY_FLAGS \\
    --out $OUT_CONTAINER/results_v3_${jobname}.json

apptainer exec \\
  --bind \$HOME/A2rchi:/workspace \\
  --bind \$HOME/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/scripts/paper_benchmark/postflight.py \\
    $OUT_CONTAINER/results_v3_${jobname}.json \\
    --expected-count $LIMIT \\
    --expected-tier $BENCHMARK_TIER \\
    --max-errors 0 \\
    --require-sources \\
    --manifest-out $OUT_CONTAINER/postflight_${jobname}.json
SBATCH
}

write_sbatch_qa bare
write_sbatch_qa rag
write_sbatch_v3 no-tools
write_sbatch_v3 live

submit_job() {
  local label=$1 dep="${2:-}" jid
  local sbatch_file="/tmp/run_${OUT_SUBDIR}_${label}.sbatch"
  [ -f "$sbatch_file" ] || { echo "ERROR: unknown benchmark config '$label'" >&2; exit 9; }
  chmod 600 "$sbatch_file"
  if [ -n "$dep" ]; then
    jid=$(sbatch --parsable --dependency=afterany:"$dep" "$sbatch_file")
    echo "$label: $jid (after $dep)"
  else
    jid=$(sbatch --parsable "$sbatch_file")
    echo "$label: $jid"
  fi
  printf '%s' "$jid"
}

echo "output: $OUT_HOST"
echo "mode: $DEPENDENCY_MODE"
echo "questions: $QUESTIONS_PATH"
echo "limit: $LIMIT"
echo "configs: $CONFIGS"
echo "concurrency: qa=$QA_CONCURRENCY agent=$AGENT_CONCURRENCY"
echo "slurm_resources: time=$SBATCH_TIME cpus=$SBATCH_CPUS mem=$SBATCH_MEM"
if [ -n "${ARCHI_SBATCH_NODELIST:-}" ]; then echo "slurm_nodelist: $ARCHI_SBATCH_NODELIST"; fi
if [ -n "${ARCHI_SBATCH_EXCLUDE:-}" ]; then echo "slurm_exclude: $ARCHI_SBATCH_EXCLUDE"; fi
echo "retry_flags:${RETRY_FLAGS:- none}"
echo "preflight_only: $PREFLIGHT_ONLY"
echo "knowledge_backend: $KNOWLEDGE_BACKEND"
if [ "$KNOWLEDGE_BACKEND" = "okg" ]; then
  echo "okg_services: $OKG_SERVICES_ENV_FILE -> ${CMS_OKG_HOSTNAME:-unknown}:${CMS_POSTGRES_PORT:-unknown}"
  echo "okg_import_path: ${OKG_PYTHONPATH:-unset}"
  echo "okg_read_surface: $OKG_READ_SURFACE"
  echo "okg_mcp: $OKG_MCP_COMMAND (cwd=$OKG_MCP_CWD deployment=$OKG_MCP_DEPLOYMENT_NAME)"
  echo "okg_mcp_tool_allowlist: ${OKG_MCP_TOOL_ALLOWLIST:-default}"
  echo "okg_parity_regular_live_sources: $ARCHI_OKG_PARITY_REGULAR_LIVE_SOURCES"
  echo "okg_parity_regular_live_corpus_tools: $ARCHI_OKG_PARITY_REGULAR_LIVE_CORPUS_TOOLS"
else
  echo "services: $SERVICES_ENV_FILE -> $ARCHI_DM_URL"
fi
echo "vllm: $VLLM_ENV_FILE -> $VLLM_MODEL at $VLLM_URL"

JOB_SUMMARY=""
FIRST_LABEL=""

case "$DEPENDENCY_MODE" in
  parallel)
    for label in $CONFIGS; do
      jid=$(submit_job "$label" | tail -1)
      JOB_SUMMARY="${JOB_SUMMARY}${label}=${jid} "
      [ -n "$FIRST_LABEL" ] || FIRST_LABEL="$label"
    done
    ;;
  chain)
    dep=""
    for label in $CONFIGS; do
      jid=$(submit_job "$label" "$dep" | tail -1)
      JOB_SUMMARY="${JOB_SUMMARY}${label}=${jid} "
      [ -n "$FIRST_LABEL" ] || FIRST_LABEL="$label"
      dep="$jid"
    done
    ;;
  *)
    echo "ERROR: ARCHI_DEPENDENCY_MODE must be chain or parallel, got $DEPENDENCY_MODE" >&2
    exit 8
    ;;
esac

[ -n "$JOB_SUMMARY" ] || { echo "ERROR: ARCHI_CONFIGS selected no benchmark configs" >&2; exit 10; }

echo
echo "jobs: $JOB_SUMMARY"
echo
echo "=== first sbatch ($FIRST_LABEL) preview ==="
sed -E 's#(--env OKG_DSN=)[^ ]+#\1***#' "/tmp/run_${OUT_SUBDIR}_${FIRST_LABEL}.sbatch" | head -30
echo
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M %R"
