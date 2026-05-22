#!/bin/bash
# Submit a CPU sbatch on ORCD that runs the 260q driver against the live
# archi-services + vllm jobs. Runs inside the data-manager .sif (which has
# all the langchain/langgraph deps baked in).
set -eu

LIMIT=${LIMIT:-3}   # default: 3 questions for smoke; override LIMIT=260 for full

ssh orcd-login "
cat > /tmp/run_260q.sbatch <<'SBATCH'
#!/bin/bash
#SBATCH --job-name=archi-bench
#SBATCH --output=/home/mohoney/archi-bench.%j.out
#SBATCH --time=08:00:00
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --nodes=1

set -euo pipefail

module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null

# Source endpoint files
. \$HOME/archi-services.env
. \$HOME/archi-vllm.env

echo \"=== endpoints ===\"
echo \"  ARCHI_DM_URL=\$ARCHI_DM_URL\"
echo \"  ARCHI_RUCIO_MCP_URL=\$ARCHI_RUCIO_MCP_URL\"
echo \"  VLLM_URL=\$VLLM_URL\"
echo \"  VLLM_MODEL=\$VLLM_MODEL\"

DM_SIF=\$HOME/.archi-bundle-state/sif/archi-data-manager.sif
REPO=\$HOME/A2rchi
SECRETS=\$HOME/.archi-bundle-state/bundle/secrets/archi

echo
echo \"=== launching driver inside dm container ===\"
apptainer exec \\
  --bind \$REPO:/workspace \\
  --bind \$SECRETS:/secrets:ro \\
  --bind /home/mohoney/bench_out:/bench_out \\
  --env ORCD_REPO=/workspace \\
  --env ARCHI_SECRETS_DIR=/secrets \\
  --env ARCHI_DM_URL=\$ARCHI_DM_URL \\
  --env ARCHI_RUCIO_MCP_URL=\$ARCHI_RUCIO_MCP_URL \\
  --env VLLM_URL=\$VLLM_URL \\
  --env VLLM_MODEL=\$VLLM_MODEL \\
  --env ORCD_OUT_DIR=/bench_out/run_260q_orcd \\
  --env PYTHONPATH=/workspace \\
  --env CC=/usr/bin/gcc --env CXX=/usr/bin/g++ \\
  \$DM_SIF \\
  python3 /workspace/.scratch/run_260q_orcd.py --limit \$LIMIT_OVERRIDE --tool-set \$TOOL_SET_OVERRIDE --concurrency \$CONCURRENCY_OVERRIDE
SBATCH
chmod +x /tmp/run_260q.sbatch

mkdir -p ~/bench_out

JOBID=\$(sbatch --parsable --export=ALL,LIMIT_OVERRIDE=$LIMIT,TOOL_SET_OVERRIDE=${TOOL_SET:-no-tools},CONCURRENCY_OVERRIDE=${CONCURRENCY:-1} /tmp/run_260q.sbatch)
echo \"submitted job \$JOBID\"

echo
echo \"=== polling (up to ~5 min) ===\"
for i in \$(seq 1 30); do
  STATE=\$(squeue -j \"\$JOBID\" -h -o \"%T %N\" 2>/dev/null)
  if [ -z \"\$STATE\" ]; then
    echo \"  iter \$i: job left queue\"; break
  fi
  echo \"  iter \$i: \$STATE\"
  sleep 10
done

echo
LATEST=\$(ls -t ~/archi-bench.*.out 2>/dev/null | head -1)
echo \"=== log: \$LATEST ===\"
tail -80 \"\$LATEST\"
"
