# Change: Replace `search_local_files` with a `grep` tool backed by ripgrep

## Why

Tool-time profiling across the v9 grading benchmark (5 configs × 53 questions) shows `search_local_files` is overwhelmingly the dominant wall-clock cost — and most of that cost is wasted on a confused tool surface.

Numbers from the actual run:

| tool | calls | total time | mean | p50 | p95 | Pearson(dur, output_size) |
|---|---:|---:|---:|---:|---:|---:|
| **`search_local_files`** | 1,038 | **314.5 min** | **18.18 s** | 20.46 s | 26.04 s | **−0.08** |
| `fetch_catalog_document` | 925 | 23.1 min | 1.50 s | 0.82 s | 4.64 s | −0.17 |
| `search_vectorstore_hybrid` | 1,065 | 18.0 min | 1.01 s | 0.65 s | 2.32 s | −0.00 |
| `search_metadata_index` | 163 | 3.0 min | 1.12 s | 0.66 s | 2.62 s | −0.02 |

Live MonIT/Rucio/condor tools combined: ~85 calls and <2 min total across the entire run. The corpus search is the bottleneck.

Three findings:

1. **The cost is the corpus walk, not the result.** Pearson(duration, output size) is ≈ 0. p95/p50 is 1.27× — every call pays the same scan price whether it returns 41 chars or 203,150 chars. Looking at `src/interfaces/uploader_app/app.py:489-542`, the grep endpoint iterates `catalog.iter_files()`, reads each file from disk via `load_text_from_path`, and runs Python regex over the full text. No index. No caching. **44% of calls return < 200 chars** (the "no match" placeholder) and still take 18 s — 8 minutes per config burned on empty queries.

2. **80% of `search_local_files` calls duplicate `search_vectorstore_hybrid` queries on the same question.** Of 196 questions where `search_local_files` fired, 156 also queried `search_vectorstore_hybrid` for the same or near-same query (gpt-5.5/live q1: `50513`, `scramscriptfailure` to both; q3: `account set-limits`, `subscribe data` to both). The agent is double-querying because the tool name `search_local_files` is semantically ambiguous — the LLM doesn't know whether to send literal or semantic patterns to it, so it sends both.

3. **`search_metadata_index` does ILIKE on `extra_text`** (`catalog_postgres.py:336-342`) — an 8-way `%pattern%` against text columns including a flattened metadata blob. Without a GIN tsvector index this is a sequential scan; the 2.62 s p95 reflects that.

The naming problem is the root cause of the redundancy. LLMs have a deep prior on `grep` from training: every model has seen millions of `grep -i`, `grep -E`, `grep -A 3` invocations. Giving the agent an honestly-named tool with the standard grep interface lets it leverage that prior — it will send literal patterns to `grep`, semantic queries to `search_vectorstore_hybrid`, and stop double-querying.

## What Changes

### 1. Drop `search_local_files`; add `grep`

The LangChain tool currently called `search_local_files` SHALL be removed entirely. In its place, a new tool named `grep` SHALL be registered with a standard grep-style interface:

```
grep(
  pattern: str,                  # required — string or regex
  ignore_case: bool = False,     # -i
  fixed_strings: bool = False,   # -F — literal, no regex
  context: int = 0,              # -C — symmetric before/after lines
  max_count: int = 3,            # --max-count per file
  files_only: bool = False,      # -l — paths only, no snippets
  limit: int = 5,                # cap on files returned
)
```

Tool description (short, mimics the first line of `man grep`):

> Literal/regex search for PATTERN in catalogued documents. Returns matching lines grouped by file with line numbers and optional context. Use this for exact-string matches (error codes, ticket IDs, log lines, file paths); use `search_vectorstore_hybrid` for semantic / paraphrased queries.

The "use `search_vectorstore_hybrid` for semantic queries" pointer is the part the current tool surface is missing. The agent will route its own queries correctly once it knows what each tool is for.

No deprecation alias. Old configs and prompts that reference `search_local_files` SHALL fail loud, not silently forward.

### 2. Replace the Python regex scan with ripgrep

The `/api/catalog/search?mode=grep` endpoint SHALL invoke `rg` (ripgrep) as a subprocess against the catalogued corpus directory instead of looping in Python. The flag mapping:

- `pattern` → `-e <pattern>`
- `ignore_case=true` → `-i`
- `fixed_strings=true` → `-F`
- `context=N` → `--context N`
- `max_count=N` → `--max-count N`
- `files_only=true` → `-l`
- `limit=N` → cap on the number of files reported (post-processing of `rg --json` output)

Output SHALL be parsed from `rg --json` and rendered into the response shape `{"hash", "path", "metadata", "matches"[], "snippet"}` already expected by callers.

If the `rg` binary is missing at runtime, the endpoint SHALL log one WARN line per process start and fall through to the existing Python loop. The data-manager container image SHALL be updated to install `ripgrep`.

### 3. LRU cache for `fetch_catalog_document`

`fetch_catalog_document` SHALL gain a process-local LRU cache keyed by `(resource_hash, max_chars)`. Default 256 entries. The p95/p50 ratio of 5.7× indicates cold-page-cache misses on rarely-touched files; an LRU drops the long tail without changing semantics.

### 4. GIN tsvector index for `search_metadata`

`search_metadata` (`catalog_postgres.py:301-356`) SHALL use a Postgres `tsvector` column with a GIN index:

- Add a `documents.tsv` column generated from `display_name || source_type || url || ticket_id || file_path || original_path || relative_path || extra_text`.
- Add a `CREATE INDEX … USING GIN(tsv)` migration.
- Rewrite the free-text branch as `WHERE tsv @@ plainto_tsquery('simple', %s)`.
- Filter-key branches (`source_type:ticket`, `ticket_id:CMSPROD-1234`, etc.) keep their exact-match column predicates.

## Verification

The change SHALL be validated by re-running gpt-5.5/live (the most tool-intensive config) on grading_questions_v2 (53 questions) and comparing tool-time aggregates against the v9 baseline. Success criteria:

- `grep` total wall time ≤ 30 min (down from `search_local_files`'s 120 min for gpt-5.5/live; target ≥ 75% reduction).
- `grep` mean call duration ≤ 2 s (down from 18 s; ripgrep with mmap + SIMD + parallel walk).
- `grep` call count ≤ 0.6× the prior `search_local_files` call count, reflecting the redundancy reduction from a clearer tool name.
- `search_metadata_index` p95 ≤ 0.25 s (down from 2.62 s; ≥ 10× drop).
- `fetch_catalog_document` p95 ≤ 1.5 s (down from 4.64 s; ≥ 3× drop).
- Judge core-4 mean across the 4 judges (glm, gemini, gpt-5.5, opus) stays within 1 σ of the v9 baseline (4.63 ± 0.32) so the rename does not silently change answer quality.

The script that produced the baseline numbers (`tool_time_breakdown.py`) SHALL be promoted from `.scratch/` to `scripts/` and SHALL be the canonical verification artifact. Pre/post snapshots SHALL be committed under `bench_out/perf_snapshots/`.

## Impact

- Affected specs: **`catalog-search`** (new capability)
- Affected code:
  - `src/archi/pipelines/agents/tools/local_files.py` — remove `create_file_search_tool`; add `create_grep_tool`. The module SHALL be renamed/moved to reflect that "local_files" is no longer the right framing.
  - `src/archi/pipelines/agents/cms_comp_ops_agent.py` — register `grep` instead of `search_local_files`.
  - `src/archi/pipelines/copilot_agents/tools/file_search.py` — same rename in the copilot agent.
  - `src/archi/pipelines/copilot_agents/copilot_agent.py` — registration update.
  - `examples/agents/cms-comp-ops.md`, `examples/agents/cms-comp-ops-no-live-data.md` — system prompt updates to reference `grep` and clarify when to use it vs `search_vectorstore_hybrid`.
  - `src/interfaces/uploader_app/app.py` — rewrite the `mode=grep` branch to shell out to `rg`.
  - `src/data_manager/collectors/utils/catalog_postgres.py` — switch `search_metadata` to tsvector; LRU cache on the document-fetch path.
  - Data-manager container image — add `ripgrep` to apt install list.
  - `migrations/` — new Alembic migration for the `tsv` column and GIN index.
  - `tests/unit/test_grep_tool.py`, `tests/integration/test_catalog_search_ripgrep.py` — new.
  - `scripts/tool_time_breakdown.py` — promoted from `.scratch/`.
- Expected outcome:
  - Per-question agent wall time drops 3–5× on tool-heavy configs.
  - Agent stops double-querying — `grep` is invoked only for literal-pattern questions; semantic queries go to `search_vectorstore_hybrid` as the description directs.
  - Paper §4 "live vs no-tools" ablation becomes a cleaner comparison (less corpus-walk overhead in the denominator).

## Non-Goals

- **Compatibility shim for `search_local_files`.** Old configs and prompts referencing it SHALL fail loud (`tool not registered`) rather than silently forward — the rename is the point.
- **Re-judging v9 answers.** v9 references `search_local_files` in its tool-call traces; that is a frozen snapshot. The post-rename benchmark run is a new comparison point, not a retroactive update to v9.
- **Tuning the embedding model or BM25 weights.** Out of scope; this proposal only changes `grep` and the metadata index.
- **Optimising live MonIT / Rucio / condor tools.** Those are < 2 min total wall time across the entire 5-config run — rounding-error gains.
- **Inverted index built at ingestion time, served from Postgres FTS.** ripgrep is fast enough that an ingestion-time inverted index is not justified. Reconsider only if `grep` is still on the critical path after this change.
