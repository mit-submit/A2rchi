# ORCD H200 Notes for Fine-Tuning

Last updated: 2026-05-25.

This is the short handoff for using the ORCD H200 nodes. The existing repo
scripts are validated for vLLM serving and benchmarking; fine-tuning still needs
a dedicated training script/container.

## Access

```bash
ssh orcd-login
```

For agent-driven work, keep a shared SSH master open from the laptop:

```bash
mkdir -p ~/.ssh/cm
ssh -N \
  -o ControlMaster=yes \
  -o ControlPersist=8h \
  -o ControlPath=~/.ssh/cm/orcd-%r@%h:%p \
  orcd-login
```

Useful checks:

```bash
squeue -u "$USER" -o "%i %j %T %M %R"
sinfo -o "%P|%a|%l|%D|%N" | head -40
scontrol show partition mit_normal_gpu mit_preemptable mit_normal
```

## Working Slurm Shapes

Full H200 node:

```bash
#SBATCH --partition=mit_normal_gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=400G
#SBATCH --gres=gpu:h200:8
#SBATCH --time=06:00:00
```

Smaller H200 job, used successfully for Qwen serving:

```bash
#SBATCH --partition=mit_preemptable
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=200G
#SBATCH --gres=gpu:h200:2
#SBATCH --time=06:00:00
```

For fine-tuning, start with the 2xH200 shape. Move to 8xH200 only after a smoke
test proves data loading, checkpointing, resume, and GPU utilization.

CPU-side Archi services used:

```bash
#SBATCH --partition=mit_normal
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=12:00:00
```

Benchmark CPU jobs used 16 CPUs and 64 GB RAM. We saw an effective per-user CPU
cap around 128 active CPUs; pending jobs reported `QOSMaxCpuPerUserLimit`.

## Important Paths

Local:

```text
/Users/jason/projects/A2rchi
```

ORCD:

```text
~/A2rchi
~/archi-deployment-bundle-20260521.tar.zst
~/.archi-bundle-key.txt
~/.archi-bundle-state/
~/.archi-bundle-state/sif/
~/.archi-bundle-state/hf-cache/
~/.archi-bundle-state/bundle/secrets/archi/hf_token.txt
~/bench_out/
```

Runtime env files:

```text
~/archi-services.env   # CPU service endpoints
~/archi-vllm.env       # vLLM endpoint; deleted when vLLM exits
```

## Validated Serving Scripts

```text
scripts/slurm/start_vllm.sh
scripts/slurm/start_archi_services.sh
```

`start_vllm.sh` launches vLLM in Apptainer, waits for `/health`, then writes
`~/archi-vllm.env` with `VLLM_URL`, `VLLM_MODEL`, tensor parallel size, context
length, parser flags, and job id.

Known-good Qwen serving flags:

```bash
# Dense Qwen3.6-27B-FP8
VLLM_MODEL=Qwen/Qwen3.6-27B-FP8
VLLM_TENSOR_PARALLEL=2
VLLM_ENABLE_EXPERT_PARALLEL=0
VLLM_MTP_TOKENS=0
VLLM_TOOL_CALL_PARSER=qwen3_xml
VLLM_REASONING_PARSER=qwen3

# MoE Qwen3.6-35B-A3B-FP8
VLLM_MODEL=Qwen/Qwen3.6-35B-A3B-FP8
VLLM_TENSOR_PARALLEL=2
VLLM_ENABLE_EXPERT_PARALLEL=1
VLLM_MTP_TOKENS=1
VLLM_TOOL_CALL_PARSER=qwen3_xml
VLLM_REASONING_PARSER=qwen3
```

Do not enable expert parallelism for dense models.

## Minimal Fine-Tuning Starter

Use a separate training image, not the vLLM image. Suggested target:

```text
~/.archi-bundle-state/sif/archi-train-pytorch.sif
```

It should include PyTorch/CUDA, `transformers`, `datasets`, `accelerate`,
`peft`, `trl`, and optionally DeepSpeed/FSDP tooling.

Starter Slurm template:

```bash
#!/bin/bash
#SBATCH --job-name=archi-ft-smoke
#SBATCH --output=archi-ft-smoke.%j.out
#SBATCH --error=archi-ft-smoke.%j.out
#SBATCH --partition=mit_preemptable
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=200G
#SBATCH --gres=gpu:h200:2
#SBATCH --time=02:00:00

set -euo pipefail
module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null

REPO="$HOME/A2rchi"
SIF="$HOME/.archi-bundle-state/sif/archi-train-pytorch.sif"
HF_CACHE="$HOME/.archi-bundle-state/hf-cache"
HF_TOKEN_FILE="$HOME/.archi-bundle-state/bundle/secrets/archi/hf_token.txt"
OUT="$HOME/finetune_out/ft-smoke-${SLURM_JOB_ID}"
TMP="/tmp/$USER-ft-smoke-${SLURM_JOB_ID}"
mkdir -p "$OUT" "$TMP" "$HF_CACHE"

if [ -s "$HF_TOKEN_FILE" ]; then
  export HUGGING_FACE_HUB_TOKEN="$(cat "$HF_TOKEN_FILE")"
fi

export NCCL_DEBUG=INFO
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True
export TOKENIZERS_PARALLELISM=false

apptainer exec --nv --no-eval \
  --bind "$REPO:/workspace" \
  --bind "$HF_CACHE:/hf-cache" \
  --bind "$OUT:/out" \
  --bind "$TMP:/tmp-train" \
  --env HUGGING_FACE_HUB_TOKEN="${HUGGING_FACE_HUB_TOKEN:-}" \
  "$SIF" \
  torchrun --standalone --nproc_per_node=2 \
    /workspace/scripts/finetune/train.py \
      --model_name_or_path <model> \
      --train_file <train.jsonl> \
      --output_dir /out/checkpoints \
      --bf16 true \
      --gradient_checkpointing true \
      --max_steps 20 \
      --save_steps 10
```

Recommended first run: LoRA/QLoRA, bf16, gradient checkpointing on, 10-50 steps,
small dataset, frequent checkpointing. Do not start with full fine-tuning until
memory and sharding are planned.

## Monitoring

For a running GPU job:

```bash
srun --jobid=<jobid> --overlap nvidia-smi \
  --query-gpu=index,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu \
  --format=csv
```

For vLLM jobs:

```bash
source ~/archi-vllm.env
curl -s "$VLLM_URL/metrics" | grep '^vllm:' | head
tail -f "$(ls -t ~/archi-vllm.*.out | head -1)"
```

## Gotchas

- `~/archi-vllm.env` is removed when vLLM exits; always check its job id.
- `mit_preemptable` can evict jobs; checkpoint early.
- Keep hot caches and temporary dataset transforms on `/tmp`, not NFS.
- HF token path is a file path only; never commit the token itself.
- Dense models do not use vLLM expert parallelism.
- Multi-node training has not been validated here. Start single-node.

## Next Fine-Tuning Tasks

1. Confirm current partition limits with `sinfo`/`scontrol`.
2. Build or select the training Apptainer image.
3. Add `scripts/finetune/train.py`.
4. Run a 2xH200 20-step smoke test.
5. Verify GPU utilization, checkpoint creation, resume, and sample generation.

