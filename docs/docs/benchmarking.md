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
- **Context recall**: How much of the retrieved context covers the ground truth

---

## Preparing the Queries File

Provide questions, expected answers, and correct sources in JSON format:

```json
[
  {
    "question": "What research does Christoph Paus lead at the PPC?",
    "sources": [
      "https://ppc.mit.edu/people/christoph-paus/",
      "CMSPROD-42"
    ],
    "answer": "Christoph Paus leads the PPC group, focused on high-energy particle physics.",
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
      sources_settings:
        default_match_field: ["file_name"]
      ragas_settings:
        embedding_model: OpenAI
        enabled_metrics:
          - answer_relevancy
          - faithfulness
          - context_precision
          - context_recall
        timeout: 180
        batch_size: null
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
| `mode_settings.sources_settings.default_match_field` | `["file_name"]` | Metadata fields to match sources against |
| `mode_settings.ragas_settings.enabled_metrics` | all four | Which RAGAS metrics to run (see RAGAS Settings) |
| `mode_settings.ragas_settings.timeout` | `180` | Max seconds per QA pair for RAGAS evaluation |
| `mode_settings.ragas_settings.batch_size` | Ragas default | Number of QA pairs to evaluate at once |

`archi evaluate` now requires benchmark runtime fields under `services.benchmarking`.
`services.chat_app` fields are not used for benchmark runtime configuration.

### RAGAS Settings

| Key | Default | Description |
|-----|---------|-------------|
| `embedding_model` | `OpenAI` | `OpenAI` or `HuggingFace` |
| `enabled_metrics` | all four | List of RAGAS metrics to evaluate: `answer_relevancy`, `faithfulness`, `context_precision`, `context_recall` |
| `timeout` | `180` | Max seconds per QA pair for RAGAS evaluation |
| `batch_size` | Ragas default | Number of QA pairs to evaluate at once (null for default) |

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

Results are saved as a timestamped JSON file in `out_dir`. When `--argilla` is enabled, results are also pushed to [Argilla](https://argilla.io) as an annotation dataset for team-based human grading.

### Output

In addition to the standard per-config results, A/B mode produces:

- **JSON `ab_comparison` section**: paired per-question results with winner-by-metric, plus aggregate wins/losses/ties and mean scores.

See `examples/benchmarking/ab_configs/` for sample A/B config files.

---

## Argilla Integration

After the benchmark runs and RAGAS scores are computed, results are pushed to [Argilla](https://argilla.io) as an annotation dataset. Each record shows the question, reference answer, and agent responses side-by-side with RAGAS scores as metadata. Graders annotate records in the Argilla UI (winner, quality rating, notes) and grades are exported back to JSON.

### Setup

**Option A: Use an existing Argilla instance**

1. Set Argilla credentials in your `.env` file:

    ```env
    ARGILLA_API_URL=http://your-argilla-server:6900
    ARGILLA_API_KEY=your-api-key
    ```

2. Add the `--argilla` flag when running:

    ```bash
    archi evaluate -n benchmark -cd configs/ -e .secrets.env --argilla
    ```

**Option B: Self-hosted Argilla (zero setup)**

Use `--argilla-server` to spin up a managed Argilla instance alongside the benchmark:

```bash
archi evaluate -n benchmark -cd configs/ -e .secrets.env --argilla-server
```

This starts an Argilla server at `http://localhost:6900` with auto-generated credentials. No `.env` Argilla keys needed.

### What Happens During a Benchmark Run

When `--argilla` is enabled:

1. The benchmark runs all questions through the agent as normal.
2. RAGAS evaluation scores are computed for each question-answer pair.
3. After all evaluation completes, results are **pushed to Argilla** as an annotation dataset.
4. Each record contains the question, reference answer, and agent response(s) as text fields, with RAGAS scores and timing as metadata.
5. The dataset is pre-configured with grading questions: **winner** (A/B/Tie, for A/B mode), **quality** (1-5 rating), and **notes** (free text).
6. The dataset name is saved to a state file so you don't need to copy-paste it.

### Grading in Argilla

After the benchmark completes, open the Argilla UI:

```bash
archi grade --serve
```

In the Argilla annotation interface you can:

1. View question, reference answer, and agent responses side-by-side
2. Select a winner (A/B/Tie in A/B mode)
3. Rate response quality (1-5)
4. Add free-text notes
5. Submit and advance to the next record (keyboard shortcuts: 1/2/3 for labels, Enter to submit)

Multiple graders can annotate the same dataset simultaneously. Argilla tracks inter-annotator agreement automatically.

### Pulling Grades

After annotating in Argilla, export grades locally:

```bash
# Pull grades from the last benchmark (no dataset name needed)
archi grade --export

# Open Argilla UI in browser
archi grade --serve

# Pull grades from a specific dataset
archi grade --dataset "my-benchmark-20260328-120000" --export

# Pull grades to a specific file
archi grade --dataset "my-benchmark-20260328-120000" --export --output results/grades.json
```

The exported JSON contains per-question annotations with winner, quality ratings, and notes.

### Local-Only Mode

Running `archi evaluate` without `--argilla` computes RAGAS scores locally and writes results to JSON — no Argilla interaction.
