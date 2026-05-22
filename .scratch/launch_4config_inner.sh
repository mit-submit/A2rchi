#!/bin/bash
# Inner script run on orcd-login: writes 4 sbatch files + submits a dep chain.
# Reads LIMIT/CONCURRENCY/etc from ARCHI_* env vars set by the outer ssh call.
set -euo pipefail

. $HOME/archi-services.env
. $HOME/archi-vllm.env

LIMIT="${ARCHI_LIMIT:-260}"
CONCURRENCY="${ARCHI_CONCURRENCY:-32}"
MAX_TOOL_CALLS="${ARCHI_MAX_TOOL_CALLS:-30}"
TOOL_TIMEOUT_S="${ARCHI_TOOL_TIMEOUT_S:-30}"
PER_QUESTION_TIMEOUT_S="${ARCHI_PER_QUESTION_TIMEOUT_S:-600}"

write_sbatch_qa() {
  local jobname=$1
  cat > /tmp/run_${jobname}.sbatch <<SBATCH
#!/bin/bash
#SBATCH --job-name=archi-bench-${jobname}
#SBATCH --output=$HOME/archi-bench-${jobname}.%j.out
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
  --env VLLM_URL=$VLLM_URL \\
  --env VLLM_MODEL=$VLLM_MODEL \\
  --env ORCD_OUT_DIR=/bench_out/run_260q_orcd_v3 \\
  --env PER_QUESTION_TIMEOUT_S=$PER_QUESTION_TIMEOUT_S \\
  --env CONCURRENCY_OVERRIDE=$CONCURRENCY \\
  --env PYTHONPATH=/workspace \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/.scratch/run_260q_orcd_qa.py \\
    --limit $LIMIT \\
    --tool-set ${jobname} \\
    --concurrency $CONCURRENCY \\
    --out /bench_out/run_260q_orcd_v3/results_v3_${jobname}.json
SBATCH
}

write_sbatch_v3() {
  local jobname=$1
  cat > /tmp/run_${jobname}.sbatch <<SBATCH
#!/bin/bash
#SBATCH --job-name=archi-bench-${jobname}
#SBATCH --output=$HOME/archi-bench-${jobname}.%j.out
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
  --env MAX_TOOL_CALLS=$MAX_TOOL_CALLS \\
  --env TOOL_TIMEOUT_S=$TOOL_TIMEOUT_S \\
  --env PER_QUESTION_TIMEOUT_S=$PER_QUESTION_TIMEOUT_S \\
  --env CONCURRENCY_OVERRIDE=$CONCURRENCY \\
  --env PYTHONPATH=/workspace \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$HOME/.archi-bundle-state/sif/archi-data-manager.sif \\
  python3 /workspace/.scratch/run_260q_orcd_v3.py \\
    --limit $LIMIT \\
    --tool-set ${jobname} \\
    --concurrency $CONCURRENCY \\
    --max-tool-calls $MAX_TOOL_CALLS \\
    --out /bench_out/run_260q_orcd_v3/results_v3_${jobname}.json
SBATCH
}

write_sbatch_qa bare
write_sbatch_qa rag
write_sbatch_v3 no-tools
write_sbatch_v3 live

JOB_BARE=$(sbatch --parsable /tmp/run_bare.sbatch)
echo "bare:      $JOB_BARE"

JOB_RAG=$(sbatch --parsable --dependency=afterany:$JOB_BARE /tmp/run_rag.sbatch)
echo "rag:       $JOB_RAG  (after $JOB_BARE)"

JOB_NOTOOLS=$(sbatch --parsable --dependency=afterany:$JOB_RAG /tmp/run_no-tools.sbatch)
echo "no-tools:  $JOB_NOTOOLS  (after $JOB_RAG)"

JOB_LIVE=$(sbatch --parsable --dependency=afterany:$JOB_NOTOOLS /tmp/run_live.sbatch)
echo "live:      $JOB_LIVE  (after $JOB_NOTOOLS)"

echo
echo "=== first sbatch (bare) preview ==="
cat /tmp/run_bare.sbatch | head -30
echo
echo "=== queue ==="
squeue -u $USER -o "%i %j %T %M %R"
