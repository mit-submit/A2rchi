#!/bin/bash
# Launch all 4 configs sequentially via SLURM dependency chain.
#  1. bare      QA driver, BareLLMPipeline
#  2. rag       QA driver, QAPipeline + keyword-grep retrieval, k=15
#  3. no-tools  v3 driver, custom React loop, catalog+vectorstore tools
#  4. live      v3 driver, custom React loop, full tool surface
#
# Each writes to ~/bench_out/run_260q_orcd_v3/results_v3_<tool-set>.json
# Re-running this script picks up where each crashed (idempotent).
set -eu

LIMIT=${LIMIT:-260}
CONCURRENCY=${CONCURRENCY:-32}
MAX_TOOL_CALLS=${MAX_TOOL_CALLS:-30}
TOOL_TIMEOUT_S=${TOOL_TIMEOUT_S:-30}
PER_QUESTION_TIMEOUT_S=${PER_QUESTION_TIMEOUT_S:-600}

echo "=== shipping drivers ==="
scp -q /Users/jason/projects/A2rchi/.scratch/run_260q_orcd_qa.py orcd-login:A2rchi/.scratch/run_260q_orcd_qa.py
scp -q /Users/jason/projects/A2rchi/.scratch/run_260q_orcd_v3.py orcd-login:A2rchi/.scratch/run_260q_orcd_v3.py

echo "=== clearing stale smoke result files ==="
ssh orcd-login 'cd ~/bench_out/run_260q_orcd_v3 && rm -f results_v3_bare.json results_v3_rag.json results_v3_no-tools.json results_v3_live.json && ls -la 2>/dev/null'

echo "=== submitting chain ==="
ssh orcd-login \
  ARCHI_LIMIT=$LIMIT \
  ARCHI_CONCURRENCY=$CONCURRENCY \
  ARCHI_MAX_TOOL_CALLS=$MAX_TOOL_CALLS \
  ARCHI_TOOL_TIMEOUT_S=$TOOL_TIMEOUT_S \
  ARCHI_PER_QUESTION_TIMEOUT_S=$PER_QUESTION_TIMEOUT_S \
  bash /home/mohoney/A2rchi/.scratch/launch_4config_inner.sh
