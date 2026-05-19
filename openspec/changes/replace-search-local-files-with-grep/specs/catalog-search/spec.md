## ADDED Requirements

### Requirement: Agent tool surface SHALL be named `grep` with a standard grep-style interface
The LangChain tool currently called `search_local_files` SHALL be removed. In its place a tool named `grep` SHALL be registered with the signature `grep(pattern, ignore_case=False, fixed_strings=False, context=0, max_count=3, files_only=False, limit=5)` and a description that mirrors the first line of `man grep` and points to `search_vectorstore_hybrid` for semantic queries.

#### Scenario: Agent registry exposes grep, not search_local_files
- **GIVEN** the CMS CompOps agent registers its tools at startup
- **WHEN** the tool registry is inspected
- **THEN** a tool named `grep` SHALL be present with the canonical signature
- **AND** no tool named `search_local_files` SHALL be registered
- **AND** any code or prompt that still references `search_local_files` SHALL fail loud (`tool not registered`) rather than silently forward

#### Scenario: Tool description directs the agent to the right alternative for semantic queries
- **GIVEN** the `grep` tool is registered
- **WHEN** the LLM reads the tool description
- **THEN** the description SHALL mention exact-string use cases (error codes, ticket IDs, log lines, file paths)
- **AND** the description SHALL direct the agent to `search_vectorstore_hybrid` for semantic / paraphrased queries

#### Scenario: System prompts reference grep, not search_local_files
- **GIVEN** the CMS CompOps agent prompts at `examples/agents/cms-comp-ops.md` and `examples/agents/cms-comp-ops-no-live-data.md`
- **WHEN** the prompts are loaded at runtime
- **THEN** any mention of the corpus-search tool SHALL use the name `grep`
- **AND** the prompts SHALL contain the same when-to-use guidance as the tool description

### Requirement: ripgrep SHALL implement the server-side grep path
The `/api/catalog/search?mode=grep` endpoint SHALL invoke `rg` (ripgrep) as a subprocess against the catalogued corpus directory instead of looping in Python. The subprocess SHALL be invoked with options that match the tool signature: `-i` for `ignore_case`, `-F` for `fixed_strings`, `--context` for `context`, `--max-count` for `max_count`, `-l` for `files_only`, and `--json` for machine-readable output. If `rg` is missing at runtime, the endpoint SHALL log one WARN line per process start and fall back to the existing Python loop.

#### Scenario: ripgrep is invoked for the grep path
- **GIVEN** a deployment with `rg` installed and a request `{pattern: "^2025-.*", ignore_case: false}`
- **WHEN** the endpoint serves the request
- **THEN** `rg --json -e '^2025-.*' --max-count <max_count> --context <context> <root>` SHALL be invoked as a subprocess
- **AND** results SHALL be parsed from `rg`'s JSON output into the existing response shape `{hits: [{hash, path, metadata, matches, snippet}], total_duration}`
- **AND** for the same query the response SHALL contain the same documents that the legacy Python loop would have returned

#### Scenario: Metadata filter pre-narrows the corpus
- **GIVEN** a request with the query string `ticket_id:CMSPROD-1234 release notes` and `fixed_strings=false`
- **WHEN** the endpoint takes the grep path
- **THEN** the parsed filter `ticket_id=CMSPROD-1234` SHALL narrow the candidate set to matching paths
- **AND** ripgrep SHALL be invoked only against those paths (passed explicitly when ≤ 1000, otherwise post-filtered by hash)

#### Scenario: Fallback to Python loop when ripgrep is absent
- **GIVEN** a deployment where `rg` is not on PATH
- **WHEN** the endpoint serves a grep request
- **THEN** a single WARN log line SHALL announce the fallback per process start
- **AND** the endpoint SHALL still complete the request using the existing Python loop
- **AND** the response SHALL be byte-for-byte identical to the pre-change behaviour

#### Scenario: Invalid regex produces HTTP 400, not a 500
- **GIVEN** a request with `pattern="[unclosed"` and `fixed_strings=false`
- **WHEN** ripgrep exits with code 2 (invalid regex)
- **THEN** the endpoint SHALL return HTTP 400 with body `{"error": "invalid_regex: <stderr>"}`
- **AND** no uncaught exception SHALL propagate

### Requirement: Indexed metadata search via tsvector
The `search_metadata` operation in `catalog_postgres.py` SHALL use a Postgres `tsvector` column with a GIN index instead of an 8-way `ILIKE %pattern%` query. The free-text branch SHALL run `WHERE tsv @@ plainto_tsquery('simple', %s)`. Explicit filter-key branches (`source_type:ticket`, `ticket_id:CMSPROD-1234`, etc.) SHALL retain their exact-match column semantics.

#### Scenario: Free-text metadata search uses the GIN index
- **GIVEN** a documents table with the `tsv` column and a GIN index
- **WHEN** `search_metadata("CMSPROD release notes")` is called with no filters
- **THEN** the SQL plan SHALL show a `Bitmap Index Scan` on the GIN index, not a sequential scan
- **AND** the call SHALL complete in under 100 ms for a 10 k-document corpus

#### Scenario: Explicit filter keys preserve their behaviour
- **GIVEN** a request `search_metadata("", filters={"source_type": "ticket", "ticket_id": "CMSPROD-1234"})`
- **WHEN** the search runs
- **THEN** the SQL SHALL contain `source_type = %s AND ticket_id = %s`
- **AND** the result SHALL be identical to the pre-change behaviour for the same filters

### Requirement: LRU cache for the document-fetch endpoint
The document-fetch endpoint SHALL cache document texts in a process-local LRU keyed by `(resource_hash, max_chars)`. Cache size SHALL be configurable; default 256 entries. The cache SHALL be transparent (same JSON response shape with or without a cache hit) and SHALL expose `cache_info()` via an admin-only diagnostic endpoint.

#### Scenario: Repeated fetches within a process hit the cache
- **GIVEN** the same `(resource_hash, max_chars)` is requested five times within one process
- **WHEN** the requests are served
- **THEN** the underlying loader SHALL be invoked at most once
- **AND** the four subsequent responses SHALL return in under 10 ms

#### Scenario: Cache size is bounded
- **GIVEN** the configured cache size is 256
- **WHEN** more than 256 distinct documents are fetched
- **THEN** the cache SHALL evict the least-recently-used entries
- **AND** memory usage SHALL stay within a few MB regardless of fetch volume
