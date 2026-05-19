# Tasks: Replace search_local_files with grep

## 1. Baseline + verification artifact
- [ ] 1.1 Promote `.scratch/tool_time_breakdown.py` to `scripts/tool_time_breakdown.py`. Document inputs (judged JSONs) and outputs (per-tool aggregates, per-config breakdown, top-10 slowest calls, no-match rate).
- [ ] 1.2 Run it against the v9 baseline and commit the snapshot at `bench_out/perf_snapshots/v9_baseline_tool_time.txt`.
- [ ] 1.3 Capture per-question wall time for gpt-5.5/live on grading_questions_v2 (53q) — the most tool-intensive config and the canonical "before" comparison.

## 2. Drop search_local_files, add grep
- [ ] 2.1 Remove `create_file_search_tool` from `src/archi/pipelines/agents/tools/local_files.py`. Rename the module to `src/archi/pipelines/agents/tools/grep.py` so the filename matches the tool name.
- [ ] 2.2 Add `create_grep_tool(catalog, *, name="grep", ...)` in the new module. The LangChain tool function signature SHALL be:
  ```
  grep(pattern, ignore_case=False, fixed_strings=False, context=0,
       max_count=3, files_only=False, limit=5)
  ```
- [ ] 2.3 Tool description (short, mirrors `man grep`): "Literal/regex search for PATTERN in catalogued documents. Returns matching lines grouped by file with line numbers and optional context. Use this for exact-string matches (error codes, ticket IDs, log lines, file paths); use `search_vectorstore_hybrid` for semantic / paraphrased queries."
- [ ] 2.4 Update agent registrations: `src/archi/pipelines/agents/cms_comp_ops_agent.py` registers `grep` in place of `search_local_files`.
- [ ] 2.5 Update the copilot agent: `src/archi/pipelines/copilot_agents/tools/file_search.py` (rename/replace), `src/archi/pipelines/copilot_agents/copilot_agent.py` (registration).
- [ ] 2.6 Update agent system prompts: `examples/agents/cms-comp-ops.md` and `examples/agents/cms-comp-ops-no-live-data.md` SHALL describe `grep` with the same when-to-use guidance from the tool description, and SHALL reference `search_vectorstore_hybrid` as the semantic-query alternative.
- [ ] 2.7 Delete or rewrite any tests that assert the existence of `search_local_files`. Add `tests/unit/test_grep_tool.py` covering signature, flag mapping, and the failure-loud behaviour when the tool name is missing.

## 3. ripgrep on the server
- [ ] 3.1 Add `ripgrep` to the data-manager container image (`apt-get install -y ripgrep` for Debian/Ubuntu base). Verify `rg --version` is callable inside the container.
- [ ] 3.2 Implement `_run_ripgrep(pattern, root, *, ignore_case, fixed_strings, context, max_count, limit)` in `src/interfaces/uploader_app/app.py` that shells out to `rg --json -e <pattern> ...`, streams its JSON output, and yields hits in the existing `{"hash", "path", "metadata", "matches", "snippet"}` shape.
- [ ] 3.3 Replace the Python loop in `api_catalog_search` (`mode == "grep"` branch) with `_run_ripgrep`. Pre-existing metadata-filter behaviour (`source_type:ticket`, etc.) SHALL still apply via the `_parse_metadata_query` step before invoking ripgrep; metadata pre-filtering yields a candidate hash set passed to rg as a path-list filter (or, for small sets, narrows the corpus root).
- [ ] 3.4 Fallback path: catch `FileNotFoundError` on `rg` not found and `subprocess.TimeoutExpired`; log a WARN line once per process and fall through to the existing Python loop. Unit test exercises this via `monkeypatch` removing rg from PATH.
- [ ] 3.5 Map invalid regex (rg exit code 2) to HTTP 400 `{"error": "invalid_regex: <stderr>"}`, mirroring the current behaviour.
- [ ] 3.6 Integration test `tests/integration/test_catalog_search_ripgrep.py`: fixture corpus of 10 small files, run a regex query, assert (a) ripgrep is invoked, (b) results match the legacy Python-loop output for the same query, (c) wall time is at least 5× faster on the fixture.

## 4. LRU cache for fetch_catalog_document
- [ ] 4.1 Wrap the document-fetch underlying loader with `functools.lru_cache(maxsize=256)` keyed on `(resource_hash, max_chars)`.
- [ ] 4.2 Add a config knob `catalog.document_cache_size` (default 256) read at process start.
- [ ] 4.3 Expose `cache_info()` via an admin-only debug endpoint `/api/catalog/_diag/cache` so an operator can verify hit-rate in production.
- [ ] 4.4 Unit test: same `(hash, max_chars)` fetched 5× hits the underlying loader once; LRU eviction kicks in past `maxsize`.

## 5. GIN tsvector index for metadata search
- [ ] 5.1 Author an Alembic migration that adds `documents.tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(display_name,'') || ' ' || coalesce(source_type,'') || ' ' || coalesce(url,'') || ' ' || coalesce(ticket_id,'') || ' ' || coalesce(file_path,'') || ' ' || coalesce(original_path,'') || ' ' || coalesce(relative_path,'') || ' ' || coalesce(extra_text,''))) STORED` and `CREATE INDEX CONCURRENTLY … USING GIN(tsv)`.
- [ ] 5.2 Rewrite the free-text branch of `search_metadata` (`catalog_postgres.py`) to use `WHERE tsv @@ plainto_tsquery('simple', %s)`. Keep filter-key branches unchanged.
- [ ] 5.3 Unit test: a query hitting 1k+ docs in a synthetic corpus completes in <100 ms via tsvector; same query via ILIKE takes >500 ms.

## 6. Verification on a real benchmark run
- [ ] 6.1 Build a deployment of the data-manager service with the changes applied. Re-run `archi evaluate` for the gpt-5.5/live config on grading_questions_v2 (53q).
- [ ] 6.2 Run `scripts/tool_time_breakdown.py` on the new result file. Compare against `bench_out/perf_snapshots/v9_baseline_tool_time.txt`. Confirm:
  - `grep` total wall time ≤ 30 min for gpt-5.5/live (vs 120 min for `search_local_files` in v9).
  - `grep` call count ≤ 0.6× the prior `search_local_files` call count.
  - `grep` mean call duration ≤ 2 s.
  - `search_metadata_index` p95 ≤ 0.25 s.
  - `fetch_catalog_document` p95 ≤ 1.5 s.
- [ ] 6.3 Run the four LLM judges (glm-5.1, gemini-3.1-pro, gpt-5.5, opus-4.7) on the new gpt-5.5/live result. Confirm core-4 mean stays within 1 σ of the v9 baseline (4.63 ± 0.32). A drop means the rename + tool-description change is silently producing different answers — investigate before merging.
- [ ] 6.4 Commit the post-change snapshot at `bench_out/perf_snapshots/replace-search-local-files-with-grep.txt`.

## 7. Documentation
- [ ] 7.1 Update `AGENTS.md` and any contributor docs that reference the agent tool list.
- [ ] 7.2 Brief note in `paper/` (the runtime / tool-time discussion section) describing the v9 → post-rename tool-time profile. Frames it as "we replaced an ambiguous corpus-search tool with a standard-interface grep; agent stopped double-querying; wall time dropped 3-5×". This is the kind of engineering result that fits the paper's "lessons learned" section.
