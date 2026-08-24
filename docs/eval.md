# archi.eval — the evaluation framework (W5 foundation)

`archi.eval` answers one question: **does asking an OKG-backed system
beat asking a plain LLM, and at what cost?** It runs a set of QA
questions against one or more answering configurations, scores the
answers, and prints a per-configuration report with pass rates, token
and cost totals, latency, and the OKG generation the answers were
produced against.

It ships in the `archi` wheel (`python/archi/eval/`) and needs no
running service: everything external — the LLM client, the MCP
session, the judge — is injected, so the framework itself is offline
and testable.

## The two nouns

**Atom** — one QA unit: a question plus the criteria a correct answer
must meet. A *static* atom's criteria are literal. A *live-state* atom
additionally carries an `oracle`: MCP tool calls whose live results
supply the expected value at run time (ported from PR #608, where the
answer to "how many downtimes are open right now?" cannot be frozen
into a dataset).

**Arm** — one answering configuration: a raw LLM, an OKG deployment
over MCP, a chat UI, a coding agent. The engine runs *atoms x arms*, so
one run compares arms head to head on identical questions. The arm is
the v3 restructuring: the v2 PRs hard-wired a single tested agent.

## Running it

```bash
# what can answer, and what each needs configured
python -m archi.eval list-arms

# one arm
python -m archi.eval run --arm raw-llm \
    --dataset python/tests/eval/fixtures/qa_smoke.yaml \
    --arm-config raw-llm.yaml

# two arms, compared in one report, pinned to a known generation
python -m archi.eval run \
    --arm raw-llm --arm okg-mcp \
    --arm-config raw-llm.yaml --arm-config okg.yaml \
    --dataset atoms.yaml \
    --generation gen:20260821T155948019759Z:56ad3d3dafec \
    --format json --output report.json
```

`--arm` repeats; `--arm-config` files pair with `--arm` flags
positionally. Config files are JSON or YAML objects. `--format md`
(default) prints the markdown report; `--format json` prints the report
dict, and `--output` writes it to a file.

Programmatic use is the same engine without the CLI's restrictions —
this is how you supply a grader or a live MCP invoker today:

```python
from archi.eval import create_arm, load_dataset, run_eval, build_report

run = run_eval(
    load_dataset("atoms.yaml"),
    [create_arm("raw-llm", {"model": "...", "client": my_client})],
    grader=my_grader,             # optional, see "LLM grading"
    oracle_invoker=my_invoker,    # required for live-state atoms
    generation_id="gen:...",
)
report = build_report(run)
```

## The arms

| id | what it is | config keys |
|---|---|---|
| `raw-llm` | no-context LLM baseline — the floor other arms must beat | `model`, `client`, `system_prompt` |
| `okg-mcp` | an OKG deployment answering over its MCP tools | `deployment`, `dsn`, `mcp_endpoint`, `model`, `ask_tool`, `invoke` |
| `openwebui-chat` | **stub** — an OpenWebUI chat deployment | `base_url`, `api_key_env`, `model` |
| `codex` | **stub** — a terminal coding agent | `command`, `workdir`, `model` |

No provider SDK is vendored. `client` (raw-llm) and `invoke` (okg-mcp)
accept either a Python callable or a `module:attr` dotted path, so the
CLI can reach an injected client that lives in operator code. Secrets
are named, never inlined: `api_key_env` holds the *name* of an
environment variable.

A misconfigured arm raises `NotConfiguredError` and **fails the run**.
It never degrades into zero scores — a setup mistake that quietly
scores as "the arm got everything wrong" would poison the comparison.

### Adding an arm

Write a class with `name`, `describe()`, and
`answer(atom, ctx) -> AnswerRecord`, then register its factory:

```python
from archi.eval.arms import AnswerRecord, register_arm

MY_CONFIG = {"endpoint": "service URL (required)"}

@register_arm("my-arm", summary="answers via ...", config_keys=MY_CONFIG)
class MyArm:
    name = "my-arm"

    def __init__(self, config):
        self.endpoint = config["endpoint"]

    def describe(self):
        return f"my service at {self.endpoint}"

    def answer(self, atom, ctx):
        return AnswerRecord(
            atom_id=atom.id, arm=self.name, answer=...,
            latency_ms=..., prompt_tokens=..., cost_usd=...,
            generation_id=...,   # when the arm touched an OKG deployment
        )
```

Fill in the optional cost/latency/generation fields when the backend
reports them — every rollup in the report reads them straight off the
`AnswerRecord`s. Raising inside `answer` is fine: the engine records an
`execution_failed` result and keeps going.

## Dataset format

JSON or YAML, either a bare list of atoms or an object with `atoms`
and an optional `schema_version: archi-eval-v1`. Validation is strict
and errors name the offending field, e.g. `atom 3 (id 'q7').checks[0]:
must set exactly one of 'value' or 'value_from'`.

```yaml
schema_version: archi-eval-v1
atoms:
  - id: cmssw-latest-14x                 # required, unique
    question: "Which CMSSW 14_0 release is the latest announced?"
    tags: [catalog, cmssw]               # optional, free-form
    answer: "CMSSW_14_0_7"               # optional reference answer
    checks:                              # deterministic criteria
      - kind: exact                      # exact | contains | regex
        value: "CMSSW_14_0_7"
        case_sensitive: true             # default true

  - id: xrootd-fallback
    question: "Explain what the CMS xrootd fallback mechanism does."
    gold_facts:                          # graded criteria (needs a grader)
      - {id: A1, text: "A failed local open triggers it.", required: true}
      - {id: A2, text: "It streams from another site.", required: false}

  - id: live-open-downtimes              # live-state atom
    question: "How many GOCDB downtimes are open for T2_US_MIT?"
    oracle:
      kind: mcp
      calls:
        - id: c1
          tool: archi_gocdb_open_downtimes
          arguments: {site: T2_US_MIT}
          answer_fields: {count: /summary/open_count}   # RFC 6901 pointers
    checks:
      - kind: contains
        value_from: /c1/count            # expected value comes from live state
```

Every atom needs at least one criterion (`checks`, `gold_facts`, or
both). `value_from` is only legal on an atom that has an `oracle`. At
least one gold fact must be `required: true`.

`python/tests/eval/fixtures/qa_smoke.yaml` is the shipped 7-atom
example (five static, one mixed, one live).

## Scoring

**Deterministic checks** run first and need no model:

- `exact` — whitespace-trimmed equality;
- `contains` — substring;
- `regex` — `re.search` over the answer.

`case_sensitive: false` lowercases both sides (for `regex` it sets
`IGNORECASE`). An atom's check score is the fraction that passed; it
`passed` only if all did.

**Graded gold facts** use PR #596's math, unchanged: each fact is
judged `entailed` (+1), `not_mentioned` (0), or `contradicted` (-1);
`atom_score = max(0, sum / count)`; `required_fact_recall` is the
entailed share of required facts; the atom passes only when *every*
required fact is entailed. A `unjudgeable` verdict scores 0 (v2 raised).

When an atom has both, its score is the mean of the two parts and it
passes only if both parts pass.

**Live-state atoms** follow PR #608: resolve the oracle, ask the arm,
resolve again, and score only if the canonical-JSON SHA-256 of the
resolved answer is identical. Otherwise the result is quarantined
(`answer_changed`), as is a failing oracle (`oracle_failed`) — a
question whose ground truth moved mid-run tells you nothing about the
arm.

Result statuses: `scored`, `execution_failed` (the arm raised),
`evaluation_failed` (the grader raised or broke its contract),
`ungraded` (gold facts with no grader injected), `oracle_failed`,
`answer_changed`. Pass rates are computed over `scored +
execution_failed` — a crash counts against an arm, a quarantined live
atom does not.

## Generation pinning and cost

Every report header carries the OKG generation the answers were
produced against. `--generation` wins if given; otherwise the engine
takes the `generation_id` values the arms reported. If arms disagree
(or an explicit pin contradicts what the arms reported), the report is
flagged `generation_conflict` — the comparison is not apples to apples.

Token, cost, and latency rollups come from the `AnswerRecord`s. For
substrate-side truth, `archi.eval.report.sum_llm_calls(dsn, since=...,
until=...)` sums the deployment's `okg.llm_calls` rows over the run
window, optionally filtered by `deployment_name` and `generation_id`.
The table contract was read from the okg checkout
(`src/okg/substrate/db/schema.sql`): columns `ts`, `deployment_name`,
`caller`, `model`, `prompt_tokens`, `completion_tokens`,
`total_tokens`, `cost_usd`, `latency_ms`, `success`, `generation_id`.
`psycopg` is imported lazily from the okg host environment and is
deliberately not an `archi` dependency; `connect=` is injectable.

## What is deliberately stubbed, and why

- **`okg-mcp` live wiring.** The adapter seam (`invoke`) and config are
  in place and tested with an injected invoker; opening a real MCP
  session is not implemented. Doing it properly means settling per-user
  MCP auth (okg#1180) and which tool an instance exposes as its
  question-answering entry point — both moving on the OKG side.
- **`openwebui-chat` and `codex`.** Registered with config schemas and
  docstrings naming the exact wiring each needs, so the registry shape
  is proven by more than one real arm without committing to two
  integrations before the comparison design is settled.
- **LLM grading.** PR #596's judge interface is ported and
  fixture-tested (`Grader.judge` -> one `Judgment` per gold fact,
  validated by the engine), but no judge model is wired and #596's
  comparator prompt is not carried over. Deterministic checks come
  first deliberately: they are reproducible and free. Wiring a judge is
  an injection, not a code change.
- **The real golden sets.** Not imported. They live in the cms-compops
  instance repo in known-divergent versions that must be reconciled
  before they become the v3 evaluation baseline. Only the synthetic
  smoke fixture ships here.

## Provenance

Restructured port of two v2-era PRs, neither of which merged:

- **PR #596** (`feat/archi-eval-command`) — atom-based QA engine.
  Kept: the gold-fact judging model and scoring math, strict dataset
  validation with contextual errors, the markdown report shape,
  lifecycle-status accounting. Dropped: the workspace/manifest/phase
  machinery (`prepare` / `run` / `score` artifact directories, run
  digests, retries, job history), the click CLI, the chat-app
  evaluation routes and RBAC surfaces.
- **PR #608** (`feat/live-eval`) — MCP-backed live-state QA. Kept: the
  oracle recipe (tool calls + JSON-pointer selection), canonical-JSON
  answer hashing, the pre/post-run drift check. Dropped: the dataset
  v1/v2 migration machinery, the oracle-config/staging surfaces, the
  atom-generation review UI.

Both PRs were written against the v2 `src/` tree. Nothing here imports
it, the `mcp` SDK, or `okg`.

## Tests

```bash
/work/submit/lavezzo/okg-venv/bin/python -m pytest python/tests/eval -q
```

96 tests, no network: dataset validation, registry resolution and
`NotConfigured` behavior, scoring edge cases, the live-atom flow with a
scripted oracle, report aggregation and cost rollups, and CLI smoke.
