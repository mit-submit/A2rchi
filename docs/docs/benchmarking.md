# Benchmarking

Archi provides benchmarking functionality via the `archi evaluate` CLI command to measure retrieval and response quality.

## Evaluation Modes

Two modes are supported (can be used together):

### SOURCES Mode

Checks if retrieved documents contain the correct sources by comparing metadata fields.

- Default match field: `file_name` (configurable per-query)
- Override with `sources_match_field` in the queries file

### RAGAS Mode

Uses the [Ragas](https://docs.ragas.io/en/stable/concepts/metrics/) evaluator for four metrics:

- **Answer relevancy**: How relevant the answer is to the question
- **Faithfulness**: Whether the answer is grounded in the retrieved context
- **Context precision**: How relevant the retrieved documents are
- **Context relevancy**: How much of the retrieved context is useful

---

## Preparing the Queries File

Provide questions, expected answers, and correct sources in JSON format:

```json
[
  {
    "question": "Does Jorian Benke work with the PPC?",
    "sources": [
      "https://ppc.mit.edu/blog/2025/07/14/welcome-our-first-ever-in-house-masters-student/",
      "CMSPROD-42"
    ],
    "answer": "Yes, Jorian works with the PPC and her topic is Lorentz invariance.",
    "source_match_field": ["url", "ticket_id"]
  }
]
```

| Field | Required | Description |
|-------|----------|-------------|
| `question` | Yes | The question to ask |
| `sources` | Yes | List of source identifiers (URLs, ticket IDs, etc.) |
| `answer` | Yes | Expected answer (used for RAGAS evaluation) |
| `source_match_field` | No | Metadata fields to match sources against (defaults to config value) |

See `examples/benchmarking/queries.json` for a complete example.

---

## Configuration

```yaml
services:
  benchmarking:
    agent_class: CMSCompOpsAgent
    agent_md_file: examples/agents/cms-comp-ops.md
    provider: local
    model: qwen3:32b
    ollama_url: http://host.containers.internal:7870
    queries_path: examples/benchmarking/queries.json
    out_dir: bench_out
    modes:
      - "RAGAS"
      - "SOURCES"
    mode_settings:
      sources:
        default_match_field: ["file_name"]
      ragas_settings:
        embedding_model: OpenAI
```

| Key | Default | Description |
|-----|---------|-------------|
| `agent_class` | — | Pipeline/agent class to run for benchmark questions |
| `agent_md_file` | — | Path to a single agent markdown file |
| `provider` | — | Provider used for benchmark question answering |
| `model` | — | Model used for benchmark question answering |
| `ollama_url` | — | Ollama base URL when `provider: local` |
| `queries_path` | — | Path to the queries JSON file |
| `out_dir` | — | Output directory for results (must exist) |
| `modes` | — | List of evaluation modes (`RAGAS`, `SOURCES`) |
| `mode_settings.ragas_settings.timeout` | `180` | Max seconds per QA pair for RAGAS evaluation |
| `mode_settings.ragas_settings.batch_size` | Ragas default | Number of QA pairs to evaluate at once |

`archi evaluate` now requires benchmark runtime fields under `services.benchmarking`.
`services.chat_app` fields are not used for benchmark runtime configuration.

### RAGAS Settings

| Key | Description |
|-----|-------------|
| `embedding_model` | `OpenAI` or `HuggingFace` |

---

## Running

Evaluate one or more configurations:

```bash
# Single config file
archi evaluate -n benchmark -c config.yaml -e .secrets.env

# Directory of configs (for comparing hyperparameters)
archi evaluate -n benchmark -cd configs/ -e .secrets.env

# With GPU support
archi evaluate -n benchmark -c config.yaml -e .secrets.env --gpu-ids all
```

Make sure the `out_dir` exists before running.

---

## Results

Results are saved in a timestamped subdirectory of `out_dir` (e.g., `bench_out/2042-10-01_12-00-00/`).

To analyze results, see `scripts/benchmarking/` which contains:

- Plotting functions
- An IPython notebook with usage examples (`benchmark_handler.ipynb`)

---

## A/B Comparison Mode

Compare two agent configurations side-by-side by running exactly two config files with `ab_mode: true`.

### Setup

Create two config files that differ in the dimension you want to test (model, provider, agent class, etc.) and set `ab_mode: true` in the benchmarking section of each:

```yaml
# config_a.yaml
services:
  benchmarking:
    ab_mode: true
    agent_class: CMSCompOpsAgent
    provider: openai
    model: gpt-4o
    queries_path: examples/benchmarking/queries.json
    out_dir: bench_out
    modes:
      - "SOURCES"
```

```yaml
# config_b.yaml
services:
  benchmarking:
    ab_mode: true
    agent_class: CMSCompOpsAgent
    provider: local
    model: gemma3
    queries_path: examples/benchmarking/queries.json
    out_dir: bench_out
    modes:
      - "SOURCES"
```

### Running

Place both configs in a directory and point `--config-dir` at it:

```bash
archi evaluate -n ab-test -cd examples/benchmarking/ab_configs/ -e .secrets.env
```

### Output

In addition to the standard per-config results, A/B mode produces:

- **JSON `ab_comparison` section**: paired per-question results with winner-by-metric, plus aggregate wins/losses/ties and mean scores.
- **HTML report**: side-by-side comparison showing both answers and metric scores for each question.

See `examples/benchmarking/ab_configs/` for sample A/B config files.

---

## Langfuse Integration

Optionally export benchmark results to [Langfuse](https://langfuse.com) for human annotation and grading.

### Setup

1. Set Langfuse credentials in your `.env` file:

    ```env
    LANGFUSE_SECRET_KEY=sk-lf-...
    LANGFUSE_PUBLIC_KEY=pk-lf-...
    LANGFUSE_HOST=https://cloud.langfuse.com
    ```

2. Add the `--langfuse` flag when running:

    ```bash
    archi evaluate -n benchmark -cd configs/ -e .secrets.env --langfuse
    ```

### What Gets Exported

- A **Langfuse Dataset** is created with one item per question (input = question, expected output = reference answer).
- For A/B mode: two **experiment runs** (one per config) are attached to the dataset with pre-computed answers and RAGAS scores as evaluations.
- For single-config mode: one experiment run is created.

Scores appear as Langfuse evaluations that can be reviewed, filtered, and augmented with human annotations in the Langfuse UI.
