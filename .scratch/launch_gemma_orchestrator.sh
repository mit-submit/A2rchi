#!/bin/bash
# Ship the patched start_vllm.sh + the gemma orchestrator, then nohup it.
scp -q /Users/jason/projects/A2rchi/scripts/slurm/start_vllm.sh orcd-login:A2rchi/scripts/slurm/start_vllm.sh
scp -q /Users/jason/projects/A2rchi/.scratch/orchestrate_gemma_sweeps.sh orcd-login:orchestrate_gemma_sweeps.sh
ssh orcd-login bash <<'REMOTE'
chmod +x ~/orchestrate_gemma_sweeps.sh
# Kill any prior gemma orchestrator
pkill -f orchestrate_gemma_sweeps.sh 2>/dev/null
sleep 1
nohup bash ~/orchestrate_gemma_sweeps.sh < /dev/null >> /dev/null 2>&1 &
disown
sleep 3
echo "--- gemma orchestrator process ---"
pgrep -af orchestrate_gemma_sweeps.sh
echo "--- log tail ---"
tail -10 ~/orchestrate_gemma.log 2>/dev/null
echo "--- HF token check ---"
ls -la ~/.archi-bundle-state/bundle/secrets/archi/hf_token.txt 2>&1
REMOTE
