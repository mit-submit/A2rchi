#!/bin/bash
#SBATCH --job-name=archi-vllm
#SBATCH --output=archi-vllm.%j.out
#SBATCH --error=archi-vllm.%j.out
#SBATCH --time=06:00:00                # mit_normal_gpu hard cap is 6h
#SBATCH --partition=mit_normal_gpu
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=400G
#SBATCH --gres=gpu:h200:8              # full 8-GPU H200 node (tensor_parallel=8)

# Brings up vLLM on a single ORCD H200 node, serving a model OpenAI-compatible
# on port ${VLLM_PORT:-8800}. Writes $HOME/archi-vllm.env so the benchmark
# driver can find it.
#
# Required env on submit:
#   VLLM_MODEL    HF model id (e.g. Qwen/Qwen3-30B-A3B, or a local /orcd path)
#
# Optional env:
#   VLLM_PORT             host port (default 8800)
#   VLLM_MAX_MODEL_LEN    context window (default 262144)
#   VLLM_TENSOR_PARALLEL  default 8 (matches --gres=gpu:h200:8)
#   VLLM_IMAGE            apptainer image of vllm (default: pulled from
#                         docker://vllm/vllm-openai:latest into $HOME)
#
# Usage:
#   sbatch --export=ALL,VLLM_MODEL=Qwen/Qwen3-30B-A3B scripts/slurm/start_vllm.sh

set -euo pipefail

log() { printf '[vllm] %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

[ -n "${VLLM_MODEL:-}" ] || die "VLLM_MODEL env var required"

# ORCD compute nodes need this for apptainer
if command -v module >/dev/null 2>&1; then
  module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null || true
fi
command -v apptainer >/dev/null || die "apptainer not available after 'module load'"

VLLM_PORT=${VLLM_PORT:-8800}
VLLM_MAX_MODEL_LEN=${VLLM_MAX_MODEL_LEN:-262144}
VLLM_TENSOR_PARALLEL=${VLLM_TENSOR_PARALLEL:-8}
VLLM_IMAGE=${VLLM_IMAGE:-$HOME/.archi-bundle-state/sif/vllm-openai.sif}
HF_CACHE=${HF_CACHE:-$HOME/.archi-bundle-state/hf-cache}
SIF_DIR=${SIF_DIR:-$HOME/.archi-bundle-state/sif}
mkdir -p "$HF_CACHE" "$SIF_DIR"

# If VLLM_MODEL_PATH is set, vLLM serves from that local directory instead of
# downloading from HF at startup. This is the fastpath after a one-time
# `huggingface-cli download` on login.
#
# Optional toggles:
#   VLLM_ENFORCE_EAGER=1   skip CUDA graph capture (1-3 min faster cold start,
#                          ~10-20% throughput penalty — fine for benchmarks)
#   VLLM_MAX_NUM_SEQS=N    cap concurrent sequences (smaller graph capture set)
#   VLLM_GPU_MEM_UTIL=F    gpu-memory-utilization (default 0.92)
VLLM_GPU_MEM_UTIL=${VLLM_GPU_MEM_UTIL:-0.92}
VLLM_EXTRA_ARGS=()
if [ "${VLLM_ENFORCE_EAGER:-0}" = "1" ]; then
  VLLM_EXTRA_ARGS+=(--enforce-eager)
fi
if [ -n "${VLLM_MAX_NUM_SEQS:-}" ]; then
  VLLM_EXTRA_ARGS+=(--max-num-seqs "$VLLM_MAX_NUM_SEQS")
fi
# Enable OpenAI-compatible tool calling (auto choice + parser).
# Default parser: qwen3_xml (Qwen3.x family emits XML-tagged tool calls).
if [ "${VLLM_ENABLE_TOOL_CHOICE:-1}" = "1" ]; then
  VLLM_EXTRA_ARGS+=(--enable-auto-tool-choice --tool-call-parser "${VLLM_TOOL_CALL_PARSER:-qwen3_xml}")
fi
# Expert parallelism — recommended by vLLM Qwen3 recipe for 2+ GPU H200
# deployments. Balances MoE experts across GPUs (fixes the GPU0=99% /
# GPU1=31% imbalance seen with TP-only).
if [ "${VLLM_ENABLE_EXPERT_PARALLEL:-1}" = "1" ]; then
  VLLM_EXTRA_ARGS+=(--enable-expert-parallel)
fi
# Multi-Token Prediction speculative decoding — recipe's "latency-focused"
# config. Generates 1 speculative token per step → ~2-3x latency reduction.
# Build the JSON via printf to avoid bash quote-stripping inside array.
if [ -n "${VLLM_MTP_TOKENS:-1}" ] && [ "${VLLM_MTP_TOKENS:-1}" != "0" ]; then
  MTP_JSON=$(printf '{"method":"mtp","num_speculative_tokens":%d}' "${VLLM_MTP_TOKENS:-1}")
  VLLM_EXTRA_ARGS+=(--speculative-config "$MTP_JSON")
fi
# Disable thinking mode via CLI (cleaner than per-request extra_body)
# Skip for model families that don't support thinking mode (e.g. Gemma).
if [ "${VLLM_DISABLE_THINKING:-1}" = "1" ]; then
  THINKING_JSON='{"enable_thinking": false}'
  VLLM_EXTRA_ARGS+=(--default-chat-template-kwargs "$THINKING_JSON")
fi
# Reasoning parser — Qwen3 emits structured reasoning; non-Qwen models don't
# need this and vllm rejects unknown parsers. Override with empty string to skip.
if [ -n "${VLLM_REASONING_PARSER-qwen3}" ]; then
  VLLM_EXTRA_ARGS+=(--reasoning-parser "${VLLM_REASONING_PARSER:-qwen3}")
fi

# Build/cached the vllm sif on first use (~5-10 GB image)
if [ ! -f "$VLLM_IMAGE" ]; then
  log "Building vllm .sif (one-time, takes ~5 min for the layer pull)…"
  apptainer build "$VLLM_IMAGE" docker://vllm/vllm-openai:latest
fi

log "Host:       $(hostname)"
log "Job:        $SLURM_JOB_ID"
log "Model:      $VLLM_MODEL"
log "Model path: ${VLLM_MODEL_PATH:-(from HF cache / download)}"
log "Port:       $VLLM_PORT"
log "TP:         $VLLM_TENSOR_PARALLEL"
log "Max len:    $VLLM_MAX_MODEL_LEN"
log "GPU mem:    $VLLM_GPU_MEM_UTIL"
log "GPUs:       $CUDA_VISIBLE_DEVICES"
log "Extra args: ${VLLM_EXTRA_ARGS[*]:-(none)}"

# Decide what to pass to --model: either a local path (fast path) or the HF id
if [ -n "${VLLM_MODEL_PATH:-}" ] && [ -d "$VLLM_MODEL_PATH" ]; then
  log "Using pre-downloaded weights from $VLLM_MODEL_PATH"
  MODEL_BIND="--bind $VLLM_MODEL_PATH:/model:ro"
  MODEL_ARG=/model
else
  log "No VLLM_MODEL_PATH or path doesn't exist; vLLM will download $VLLM_MODEL from HF"
  MODEL_BIND=""
  MODEL_ARG="$VLLM_MODEL"
fi

# Run vllm in the foreground inside apptainer.
# - --nv: pass NVIDIA drivers from host into container
# - bind HF_CACHE so subsequent runs reuse downloaded weights
# - bind /tmp for any vllm temp files
# - HF_HUB_ENABLE_HF_TRANSFER=1 uses the Rust-based parallel downloader inside
#   the container (vllm-openai image ships hf_transfer)
# NFS $HOME has rename-atomicity issues that break flashinfer's TRT-LLM cubin
# caching ($HOME/.tensorrt_llm/{tmp,cache}). Apptainer's --bind over an auto-
# mounted $HOME subpath doesn't reliably work. Workaround: replace the path
# with a SYMLINK to node-local /tmp before launching. Container follows the
# symlink and writes hit local FS.
mkdir -p /tmp/$USER-trtllm/tmp /tmp/$USER-trtllm/cache
# Remove anything in the way of the symlink, but keep an existing prior dir
# as a backup in case it has cached cubins from a previous successful run.
if [ -L "$HOME/.tensorrt_llm" ]; then
  rm "$HOME/.tensorrt_llm"
elif [ -d "$HOME/.tensorrt_llm" ]; then
  mv "$HOME/.tensorrt_llm" "$HOME/.tensorrt_llm.bak.$$"
fi
ln -sfn /tmp/$USER-trtllm "$HOME/.tensorrt_llm"
log "  TRT-LLM cache: $HOME/.tensorrt_llm -> /tmp/$USER-trtllm (node-local)"
mkdir -p /tmp/$USER-cache /tmp/$USER-triton

apptainer run --nv --no-eval \
  $MODEL_BIND \
  --bind "$HF_CACHE:/root/.cache/huggingface" \
  --bind /tmp:/tmp \
  --bind /tmp/$USER-cache:$HOME/.cache \
  --bind /tmp/$USER-triton:$HOME/.triton \
  --env HUGGING_FACE_HUB_TOKEN="${HUGGING_FACE_HUB_TOKEN:-}" \
  --env HF_HUB_ENABLE_HF_TRANSFER=1 \
  --env CC=/usr/bin/gcc \
  --env CXX=/usr/bin/g++ \
  --env PATH=/usr/local/nvidia/bin:/usr/local/cuda/bin:/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin \
  --env HOME=$HOME \
  --env XDG_CACHE_HOME=$HOME/.cache \
  --env TRTLLM_CACHE_DIR=$HOME/.tensorrt_llm/cache \
  --env VLLM_BLOCKSCALE_FP8_GEMM_FLASHINFER=0 \
  --env VLLM_USE_DEEP_GEMM=0 \
  --env VLLM_MOE_USE_DEEP_GEMM=0 \
  --env VLLM_USE_TRTLLM_ATTENTION=0 \
  --env VLLM_ATTENTION_BACKEND=FLASH_ATTN \
  --env VLLM_USE_FLASHINFER_MOE_FP8=1 \
  --env VLLM_FLASHINFER_MOE_BACKEND=latency \
  "$VLLM_IMAGE" \
  --model "$MODEL_ARG" \
  --served-model-name "$VLLM_MODEL" \
  --tensor-parallel-size "$VLLM_TENSOR_PARALLEL" \
  --max-model-len "$VLLM_MAX_MODEL_LEN" \
  --host 0.0.0.0 \
  --port "$VLLM_PORT" \
  --gpu-memory-utilization "$VLLM_GPU_MEM_UTIL" \
  "${VLLM_EXTRA_ARGS[@]}" \
  &
VLLM_PID=$!

# Wait for the OpenAI endpoint to come alive (vllm takes 2-5 min to load weights)
log "Waiting for vLLM /health to be ready (up to 600s)…"
deadline=$(( $(date +%s) + 600 ))
while [ $(date +%s) -lt $deadline ]; do
  if curl -fsS -m 5 "http://127.0.0.1:${VLLM_PORT}/health" >/dev/null 2>&1; then
    log "  vLLM healthy"
    break
  fi
  sleep 5
done

# Publish endpoint
{
  echo "# Generated by start_vllm.sh at $(date -Iseconds)"
  echo "SLURM_JOB_ID=$SLURM_JOB_ID"
  echo "VLLM_HOSTNAME=$(hostname)"
  echo "VLLM_URL=http://$(hostname):${VLLM_PORT}/v1"
  echo "VLLM_MODEL=${VLLM_MODEL}"
} > "$HOME/archi-vllm.env"
log "Wrote $HOME/archi-vllm.env:"
cat "$HOME/archi-vllm.env"

# Wait for vllm to exit (or for the job to be killed)
cleanup() {
  log "Caught signal; stopping vLLM…"
  kill -TERM "$VLLM_PID" 2>/dev/null || true
  wait "$VLLM_PID" 2>/dev/null || true
  rm -f "$HOME/archi-vllm.env"
}
trap cleanup EXIT TERM INT
wait "$VLLM_PID"
