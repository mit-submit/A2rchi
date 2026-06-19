# Paper Benchmark Scripts

This directory is the stable handoff surface for the paper benchmark workflow.
It contains cleaned copies of the corrected ORCD drivers and launch wrappers.
The older `.scratch/` scripts are historical run records, not the public entry
point for collaborators.

## Contents

| File | Purpose |
|---|---|
| `run_qa.py` | Runs `bare` and `rag` with the real Archi bare/RAG pipelines. |
| `run_agent.py` | Runs `no-tools` and `live` with the corrected agent loop. |
| `launch_4config_slurm.sh` | Submits the four Qwen/vLLM configs on ORCD. |
| `launch_openai_270q_slurm.sh` | Submits the four OpenAI/GPT configs on ORCD CPU nodes. |
| `preflight.py` | Checks questions, contracts, services, prompts, corpus, and tools before running. |
| `postflight.py` | Checks result JSONs after running. |
| `judge_glm51_slurm.sh` | Stages result JSONs and submits GLM-5.1 judging. |
| `agent_tool_helpers.py` | Shared helper for Rucio, MONIT/OpenSearch, and catalog tool construction. |
| `vectorstore_tool.py` | Hybrid vectorstore tool helper used by `run_agent.py`. |

## Expected Environment

Run these scripts from an ORCD checkout mounted in the Archi Apptainer image,
normally at `~/A2rchi` on the login node and `/workspace` inside the container.

The local-model path expects:

- `~/archi-services.env` from `scripts/slurm/start_archi_services.sh`
- `~/archi-vllm.env` from `scripts/slurm/start_vllm.sh`
- `~/.archi-bundle-state/sif/archi-data-manager.sif`
- `~/.archi-bundle-state/bundle/secrets/archi/` mounted read-only

The OpenAI path does not need vLLM, but it still needs Archi services for RAG
and live-tool configurations.

## Local-Model Four-Config Run

```bash
ARCHI_QUESTIONS_PATH=/workspace/configs/submit75/curated_questions_270.json \
ARCHI_LIMIT=270 \
ARCHI_OUT_SUBDIR=run_270q_orcd_v3_qwen27b \
ARCHI_QA_CONCURRENCY=16 \
ARCHI_AGENT_CONCURRENCY=8 \
ARCHI_DEPENDENCY_MODE=parallel \
bash scripts/paper_benchmark/launch_4config_slurm.sh
```

Outputs are written under:

```text
~/bench_out/<run-name>/results_v3_bare.json
~/bench_out/<run-name>/results_v3_rag.json
~/bench_out/<run-name>/results_v3_no-tools.json
~/bench_out/<run-name>/results_v3_live.json
```

## OpenAI/GPT Four-Config Run

```bash
ARCHI_OPENAI_MODEL=gpt-5.5-2026-04-23 \
ARCHI_OUT_SUBDIR=run_270q_gpt55_openai \
ARCHI_CONFIGS="bare rag no-tools live" \
bash scripts/paper_benchmark/launch_openai_270q_slurm.sh
```

The launcher reads `openai_api_key.txt` from the standard Archi secrets
directory. Do not put API keys in this repository.

## Judge Run

```bash
GLM51_RUN_ID=270q_run1 \
GLM51_SOURCE_MODE=auto \
bash scripts/paper_benchmark/judge_glm51_slurm.sh
```

For a custom set of inputs, create a tab-separated manifest:

```text
qwen27b_live    /home/mohoney/bench_out/run_270q_orcd_v3_27b/results_v3_live.json
gpt55_live      /home/mohoney/bench_out/run_270q_gpt55_openai/results_v3_live.json
```

Then run:

```bash
GLM51_INPUT_MANIFEST=/path/to/manifest.tsv \
GLM51_EXPECTED_ROWS=270 \
bash scripts/paper_benchmark/judge_glm51_slurm.sh
```

## Safety Notes

- These scripts reference secret file paths, but contain no secret values.
- Do not commit `bench_out/`, grader lists, Argilla databases, `.env` files,
  unblinding maps, or raw production traces.
- The corrected paper tier forbids the old `read_skill` tool. Live-tool
  references are inlined into the system prompt by `run_agent.py`.
