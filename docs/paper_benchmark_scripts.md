# Paper Benchmark Scripts

Last updated: 2026-06-19.

This document maps the scripts used to generate, validate, judge, and hand off
the paper benchmark workflow. The stable interface is now
`scripts/paper_benchmark/`; older `.scratch/` files are treated as run history.

## Sharing Rules

Share the promoted scripts, this document, approved question/config files, and
sanitized run instructions. Do not share `.env` files, token files, private
grader lists, unblinding maps, raw Argilla databases, raw production traces, or
generated result directories unless the recipient is explicitly cleared for
that data.

## Primary Workflow

1. Start the Archi CPU service stack on ORCD.
2. Start a vLLM job for local open-weight models, or use the OpenAI launcher
   for GPT runs.
3. Run the four configurations: `bare`, `rag`, `no-tools`, and `live`.
4. Validate result JSONs with preflight/postflight manifests.
5. Stage result JSONs and run GLM-5.1 judging.
6. Use the paper analysis scripts to build tables and figures from judged
   outputs and human-grading exports.

The corrected ORCD/vLLM path is the source of truth for Qwen paper runs. The
OpenAI path uses the same Python drivers but does not need a vLLM/H200 job.

## Question And Contract Files

| File | Purpose |
|---|---|
| `configs/submit75/curated_questions_270.json` | Final 270-question production-trace-derived workload. |
| `configs/submit75/curated_questions.json` | Original 260-question workload, retained for provenance. |
| `configs/submit75/grading_questions_current_63_from_270.json` | 63-question comparison subset. |
| `configs/submit75/orcd_vllm_corrected_contract.json` | Corrected ORCD/vLLM run contract: tier, prompts, tools, model metadata, and corpus checks. |

## Promoted Script Surface

| Script | Role |
|---|---|
| `scripts/paper_benchmark/run_qa.py` | Corrected `bare` and `rag` driver using the real `BareLLMPipeline` and `QAPipeline`. |
| `scripts/paper_benchmark/run_agent.py` | Corrected `no-tools` and `live` agent driver with resume, trace capture, timeouts, and budget handling. |
| `scripts/paper_benchmark/launch_4config_slurm.sh` | ORCD launcher for the four local-model configs against Archi services plus vLLM. |
| `scripts/paper_benchmark/launch_openai_270q_slurm.sh` | ORCD CPU launcher for OpenAI/GPT configs against the same Archi services. |
| `scripts/paper_benchmark/preflight.py` | Validates questions, contract, prompts, service health, corpus state, and tool expectations before a run. |
| `scripts/paper_benchmark/postflight.py` | Validates result count, tier, traces, model metadata, errors, budget hits, and evidence after a run. |
| `scripts/paper_benchmark/judge_glm51_slurm.sh` | Stages result JSONs and submits GLM-5.1 judging through OpenRouter. |
| `scripts/paper_benchmark/vectorstore_tool.py` | Hybrid vectorstore tool helper used by the promoted agent driver. |
| `scripts/paper_benchmark/agent_tool_helpers.py` | Rucio/MONIT/catalog tool-construction helper used by the promoted agent driver. |

The promoted drivers are idempotent: existing successful `question_<idx>` rows
are skipped. Use `--retry-errored` or `--retry-empty` only when intentionally
replacing bad rows.

## Service Launchers

| Script | Role |
|---|---|
| `scripts/slurm/start_archi_services.sh` | Starts Postgres, data-manager, and read-only operational tools. Writes `~/archi-services.env`. |
| `scripts/slurm/start_vllm.sh` | Starts vLLM on ORCD H200s. Writes `~/archi-vllm.env`. |

`~/archi-services.env` provides `ARCHI_DM_URL`, `ARCHI_POSTGRES_URL`, and live
tool endpoints. `~/archi-vllm.env` provides `VLLM_URL`, `VLLM_MODEL`, parser
flags, tensor/expert parallel settings, and the vLLM job id. The vLLM env file
is removed when the vLLM job exits, so verify it before submitting benchmark
jobs.

## Typical ORCD Run

```bash
cd ~/A2rchi

# After archi-services and vLLM are already up:
ARCHI_QUESTIONS_PATH=/workspace/configs/submit75/curated_questions_270.json \
ARCHI_LIMIT=270 \
ARCHI_OUT_SUBDIR=run_270q_orcd_v3_qwen27b \
ARCHI_QA_CONCURRENCY=16 \
ARCHI_AGENT_CONCURRENCY=8 \
ARCHI_DEPENDENCY_MODE=parallel \
bash scripts/paper_benchmark/launch_4config_slurm.sh
```

For OpenAI/GPT runs:

```bash
cd ~/A2rchi

ARCHI_OPENAI_MODEL=gpt-5.5-2026-04-23 \
ARCHI_OUT_SUBDIR=run_270q_gpt55_openai \
ARCHI_CONFIGS="bare rag no-tools live" \
bash scripts/paper_benchmark/launch_openai_270q_slurm.sh
```

The OpenAI launcher still needs Archi services for RAG and live/tool configs.
It reads the API key from the standard Archi secrets directory inside the
container; the key itself is not stored in the script.

## Judging

The main reusable judge runner is `scripts/run_evaluation.py`. The ORCD wrapper
stages result files and submits a GLM-5.1 Slurm job:

```bash
GLM51_RUN_ID=270q_run1 \
GLM51_SOURCE_MODE=auto \
bash scripts/paper_benchmark/judge_glm51_slurm.sh
```

By default the judge wrapper stages the paper-era Qwen/GPT/Gemma paths under
`~/bench_out`. For a custom set, provide a tab-separated manifest:

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

## Human Grading

The generic Argilla integration remains in `src/utils/benchmark_argilla.py` and
`docs/docs/benchmarking.md`. The paper-specific human-grading deployment was
operational infrastructure on submit76; use `docs/argilla_human_grading_notes.md`
for the sanitized handoff. Do not commit raw grading databases, grader account
lists, OAuth secrets, or unblinding maps.

## Historical Run Scripts

Several `.scratch/` scripts remain useful provenance records: queue wrappers,
append/retry helpers, monitors, plot prototypes, and recovery orchestrators.
They should not be the colleague-facing API. If a future reproduction needs one
of them, promote a cleaned copy into `scripts/paper_benchmark/` first.
