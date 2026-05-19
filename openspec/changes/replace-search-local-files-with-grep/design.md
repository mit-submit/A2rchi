# Design: Replace search_local_files with a `grep` tool backed by ripgrep

## Decision summary

The fix is a *naming and interface* change at the tool layer, plus a *subprocess* change at the server layer. No routing logic, no compatibility shim.

```
agent calls grep(pattern, ignore_case, fixed_strings, context, max_count, files_only, limit)
        │
        ▼
/api/catalog/search (mode=grep)
        │
        ▼
   _run_ripgrep(pattern, root, …)
        │
        ▼
   rg --json -e <pattern> --max-count N --context N <root>
        │
        ▼
   parse hits → {hash, path, metadata, matches[], snippet}
```

If `rg` is missing on PATH, the endpoint logs one WARN and falls back to the legacy Python loop. That fallback is purely operational — the design assumes ripgrep is installed.

## Why the rename matters as much as ripgrep

We measured two distinct problems and only one of them is "the code is slow":

1. **The Python loop is slow.** 18 s mean per call across 1,038 calls = 314 min wasted. ripgrep replaces this directly.
2. **The agent doesn't know what `search_local_files` is for.** 80% of calls duplicate `search_vectorstore_hybrid` queries on the same question. The agent treats the tool as a generic retriever because its name doesn't constrain semantics.

Renaming to `grep` with a standard man-grep description fixes (2) at zero implementation cost. The LLM's training data is full of grep invocations — it already knows `grep -i CMSPROD-1234 docs/` means "find this literal string in those files," and that semantic queries belong elsewhere. The redundancy is *expected* to drop without any server-side routing.

Both fixes compound: each call is ~20× faster *and* there are fewer calls. The expected end state is `grep` taking ~17 min total across the 5-config run, down from `search_local_files`'s 314 min.

## Why we drop the name rather than alias it

A deprecation alias (`search_local_files` → forwards to `grep`) would be safer in a normal codebase. We are not in a normal codebase:

- The v9 benchmark answers are already frozen with `search_local_files` in their traces — no live consumer needs continuity.
- Active configs that reference the old name (system prompts, agent registration) are all in this repo and easy to update in the same PR.
- An alias would *hide* the rename from the LLM at runtime: the model would see a tool called `search_local_files` (because the alias is registered under that name to satisfy old code) and continue double-querying. The point of the change is to make the LLM see the new name.

So: no alias. Old configs fail with `tool not registered`. That's a feature.

## Why ripgrep, not Postgres FTS

For the genuine grep case (literal/regex over file contents), three options exist:

| approach | latency | regex support | ingestion overhead | implementation cost |
|---|---|---|---|---|
| Python loop (current) | 18 s | yes | none | 0 |
| ripgrep subprocess | < 1 s expected | yes (PCRE2 via `-P`) | none | small |
| Postgres FTS (tsvector + GIN) | < 100 ms | no — token-level only | re-index corpus | large |

Postgres FTS is the fastest but cannot handle arbitrary regex (`^2025-.*`, `\bWARNING\b`, etc.). Looking at the top-10 slowest current calls, ~50% are real regex queries. ripgrep is the right tool for the literal-and-regex case at acceptable speed without an ingestion-time rebuild.

We use Postgres FTS only for `search_metadata_index` (§4 of the proposal), where token-level matching is fine and the existing 8-way ILIKE is genuinely doing a sequential scan.

## The output-shape contract

Existing callers (agent + copilot agent) parse the `/api/catalog/search` response as:

```json
{
  "hits": [
    {
      "hash": "ticket_CMSPROD-359",
      "path": "/data/cms_compops/jira/ticket_CMSPROD-359.txt",
      "metadata": {"ticket_id": "CMSPROD-359", "source_type": "ticket", ...},
      "matches": [
        {"line": 12, "text": "...matching line...", "before": [], "after": []}
      ],
      "snippet": "first matching line"
    }
  ],
  "total_duration": 0.42
}
```

ripgrep's `--json` output emits `match` and `context` events with byte offsets and line numbers. We map:
- one `rg` `match` event → one entry in `matches`
- `rg` `context` events with `kind: before` / `kind: after` → `before`/`after` arrays on the prior `match`
- `matches[0].text` → `snippet`

Per-file `--max-count N` cap mirrors `max_matches_per_file`. Cross-file `limit` is applied in our post-processing of the JSON stream.

The shape is byte-for-byte identical to what the legacy Python loop produced for the same query. Existing snippet-parsing in the agent does not need to change.

## Filter pre-narrowing

The current grep path supports `_parse_metadata_query` filters like `source_type:ticket release notes` — filters narrow the candidate set to a list of paths, then grep runs only over those paths. We preserve this:

- Parse filters as today, producing a set of `(resource_hash, path)` candidates.
- For ripgrep: if the candidate set is small (≤ 1000 paths), pass them as explicit arguments. If large, fall back to invoking `rg` on the corpus root and post-filtering hits by hash. (Avoids ARG_MAX on the command line.)

## Risks and how we mitigate them

1. **The new tool name changes agent behaviour in ways we don't predict.** The agent might over-rely on `grep` for things `search_vectorstore_hybrid` would have handled. **Mitigation**: verification task §6.3 re-judges gpt-5.5/live with all 4 judges — core-4 mean must stay within 1 σ of the v9 baseline. Quality is the floor; speed is the ceiling we're trying to raise.
2. **ripgrep not in the deployment image.** **Mitigation**: explicit `FileNotFoundError` catch falls back to the Python loop with a single WARN log. Tasks include the Dockerfile change; runtime degrades gracefully if a deployment forgets.
3. **Tool-call traces in v9 reference `search_local_files`** — any longitudinal analysis script that hardcodes that string will break. **Mitigation**: v9 is a frozen reference. Post-change runs use `grep`. The tool_time_breakdown.py script reads tool names from each result file, so it works across both schemas without changes.
4. **System-prompt drift between agents.** Two agent prompts (`cms-comp-ops.md`, `cms-comp-ops-no-live-data.md`) and the copilot agent reference the old tool by name. **Mitigation**: tasks §2.5 and §2.6 enumerate every site; CI grep for `search_local_files` after the change ensures no stragglers.
5. **Heuristic-free design means the agent must "get it"**: the description text and the name together SHOULD push the model to the right tool. If we observe in the post-change run that the agent still double-queries `grep` and `search_vectorstore_hybrid`, the fix is to sharpen the tool descriptions further (not to re-introduce server-side routing).

## Out-of-scope but tempting

- **Inverted index built at ingestion time.** Better long-term than ripgrep-on-demand, but a corpus re-index is a separate change with separate verification needs. Reconsider only if ripgrep is still on the critical path.
- **Speculative parallel calls (grep + vectorstore, take first non-empty).** Cuts latency on the cold path but doubles work; the heuristic-free design already cuts cold-path cost by 20×.
- **Cross-tool query deduplication.** Could detect that the agent sent the same query to both `grep` and `search_vectorstore_hybrid` within one question and serve the second from cache. Cheap to implement but masks an agent-prompting problem rather than fixing it.
