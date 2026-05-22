#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== secrets dir contents ==="
ls ~/.archi-bundle-state/bundle/secrets/archi/ | head -20
echo
echo "=== anything matching hf/hugging/token ==="
ls ~/.archi-bundle-state/bundle/secrets/archi/ | grep -i 'hf\|hugging\|token'
echo
echo "=== huggingface-cli login state ==="
ls -la ~/.cache/huggingface/token 2>/dev/null
ls -la ~/.huggingface/ 2>/dev/null
echo
echo "=== start_vllm.sh: HF_TOKEN handling ==="
grep -n -i 'hf_token\|hugging\|HF_HOME' ~/A2rchi/scripts/slurm/start_vllm.sh | head -10
echo
echo "=== vllm tool-call parsers available (newest log) ==="
grep -i "tool.*parser\|tool_call" ~/archi-vllm.*.out 2>/dev/null | grep -v "loggers" | head -5
REMOTE
