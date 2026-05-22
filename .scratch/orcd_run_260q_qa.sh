#!/bin/bash
# Submit the QA/Bare bench driver (real QAPipeline / BareLLMPipeline).
#
# Usage:
#   LIMIT=260 TOOL_SET=bare  bash orcd_run_260q_qa.sh
#   LIMIT=260 TOOL_SET=rag   bash orcd_run_260q_qa.sh
#
# Resume by re-running the same TOOL_SET (results file is stable per tool set).
set -eu

LIMIT=${LIMIT:-3}
TOOL_SET=${TOOL_SET:-bare}             # bare | rag
CONCURRENCY=${CONCURRENCY:-32}
PER_QUESTION_TIMEOUT_S=${PER_QUESTION_TIMEOUT_S:-600}
RETRY_ERRORED=${RETRY_ERRORED:-0}
OUT_NAME=${OUT_NAME:-results_v3_${TOOL_SET}.json}

scp -q /Users/jason/projects/A2rchi/.scratch/run_260q_orcd_qa.py orcd-login:A2rchi/.scratch/run_260q_orcd_qa.py

ssh orcd-login "
cat > /tmp/run_260q_qa.sbatch <<'SBATCH'
#!/bin/bash
#SBATCH --job-name=archi-bench-qa
#SBATCH --output=/home/mohoney/archi-bench-qa.%j.out
#SBATCH --time=08:00:00
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --nodes=1

set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null

. \$HOME/archi-services.env
. \$HOME/archi-vllm.env

DM_SIF=\$HOME/.archi-bundle-state/sif/archi-data-manager.sif
REPO=\$HOME/A2rchi
SECRETS=\$HOME/.archi-bundle-state/bundle/secrets/archi
OUT_PATH=/bench_out/run_260q_orcd_v3/\$OUT_NAME_OVERRIDE

mkdir -p \$HOME/bench_out/run_260q_orcd_v3

EXTRA_FLAGS=
if [ \"\$RETRY_ERRORED_OVERRIDE\" = \"1\" ]; then
  EXTRA_FLAGS=--retry-errored
fi

echo \"=== QA-pipeline launch: tool_set=\$TOOL_SET_OVERRIDE  limit=\$LIMIT_OVERRIDE  concurrency=\$CONCURRENCY_OVERRIDE ===\"

apptainer exec \\
  --bind \$REPO:/workspace \\
  --bind \$SECRETS:/secrets:ro \\
  --bind /home/mohoney/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=\$ARCHI_DM_URL \\
  --env VLLM_URL=\$VLLM_URL \\
  --env VLLM_MODEL=\$VLLM_MODEL \\
  --env ORCD_OUT_DIR=/bench_out/run_260q_orcd_v3 \\
  --env PER_QUESTION_TIMEOUT_S=\$PER_QUESTION_TIMEOUT_S_OVERRIDE \\
  --env CONCURRENCY_OVERRIDE=\$CONCURRENCY_OVERRIDE \\
  --env PYTHONPATH=/workspace \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$DM_SIF \\
  python3 /workspace/.scratch/run_260q_orcd_qa.py \\
    --limit \$LIMIT_OVERRIDE \\
    --tool-set \$TOOL_SET_OVERRIDE \\
    --concurrency \$CONCURRENCY_OVERRIDE \\
    --out \$OUT_PATH \\
    \$EXTRA_FLAGS
SBATCH
chmod +x /tmp/run_260q_qa.sbatch

JOBID=\$(sbatch --parsable \\
  --export=ALL,LIMIT_OVERRIDE=$LIMIT,TOOL_SET_OVERRIDE=$TOOL_SET,CONCURRENCY_OVERRIDE=$CONCURRENCY,PER_QUESTION_TIMEOUT_S_OVERRIDE=$PER_QUESTION_TIMEOUT_S,RETRY_ERRORED_OVERRIDE=$RETRY_ERRORED,OUT_NAME_OVERRIDE=$OUT_NAME \\
  /tmp/run_260q_qa.sbatch)
echo \"submitted job \$JOBID\"
echo \"tail with: ssh orcd-login 'tail -f ~/archi-bench-qa.\$JOBID.out'\"
echo \"out file: ~/bench_out/run_260q_orcd_v3/$OUT_NAME\"
"
