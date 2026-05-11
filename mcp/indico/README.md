# Indico MCP sidecar

A containerized [mcp4indico](https://gitlab.cern.ch/itgpt/mcp4indico) server, deployed
alongside archi via the generic MCP sidecar mechanism.

The sidecar exposes an HTTP MCP endpoint at `/mcp` on port `8012` and gives the agent
live, tool-call access to the Indico HTTP API (event/contribution/file lookups, search,
etc.). Pinned to upstream commit `800c5fc3` (set in the [Dockerfile](Dockerfile) `MCP4INDICO_REF` ARG).

## When to use this vs. the existing IndicoScraper

| | IndicoScraper (batch) | Indico MCP (live) |
|---|---|---|
| Path | `data_manager` ingestion → embeddings → vectorstore | Agent tool call at chat time |
| Auth | Selenium + CERN SSO (fragile) | Bearer token / API key+secret |
| Best for | "what was discussed at SUSY '24" — semantic recall over many past meetings | "list contributions for event 137346" — exact, current lookups |
| Freshness | Whenever the scraper last ran | Now |
| Cost per use | Cheap (one similarity search) | One Indico API call + LLM tool roundtrip per question |

They are complementary, but the MCP path is the one we ship enabled. The MCP also
*ingests* event attachments into the same vectorstore via a shared volume — see
[Ingesting event attachments](#ingesting-event-attachments) below.

## Configuration

In your archi config (e.g. `examples/deployments/basic-agent/config.yaml`):

```yaml
services:
  chat_app:
    skills_dir: ./skills        # so the indico skill markdown gets injected once

mcp_servers:
  indico:
    transport: streamable_http
    url: http://indico-mcp:8012/mcp     # bridge-network deployments
    # url: http://localhost:8012/mcp    # use this when running with --hostmode
    build_context: ./mcp/indico
    env:
      INDICO_BASE_URL: https://indico.cern.ch
      BEARER_TOKEN: ${INDICO_BEARER_TOKEN}
      API_KEY: ${INDICO_API_KEY}
      API_SECRET: ${INDICO_API_SECRET}
    # Names a docker volume that the data-manager also mounts read-only at the
    # same path. INDICO_get_files writes downloads here, then ingest_indico_event
    # picks them up. See "Ingesting event attachments" below.
    shared_volume: indico-downloads
    skill: indico
```

Pass credentials via `--env-file` at `archi create` time:

```sh
cat > /tmp/indico-env <<'EOF'
INDICO_BEARER_TOKEN=indp_...   # Indico → Preferences → API tokens (scopes: read:legacy_api, read:user)
INDICO_API_KEY=...              # Indico → Preferences → HTTP API (40 chars)
INDICO_API_SECRET=...           # required for downloading event files
EOF

archi create --name my-archi --config <your-config>.yaml --env-file /tmp/indico-env
```

`API_KEY` / `API_SECRET` are only needed for the file-download tool (`INDICO_get_files`); a
`BEARER_TOKEN` alone is enough for everything else.

## host_mode caveat

If you run archi with `--hostmode`, PR [#557](https://github.com/archi-physics/archi/pull/557) puts the sidecar on the host
network too. In that case the chat app reaches it at `http://localhost:8012/mcp`, **not**
`http://indico-mcp:8012/mcp` (compose service DNS does not apply on the host network namespace).
Set the `url` field accordingly.

## Tools exposed

From upstream's README — the server exposes 8 tools today:

- `configure` — set tokens at runtime (we set them via env so this is rarely needed)
- `INDICO_get_user_info`
- `INDICO_search_events_by_category_id`
- `INDICO_search_events_by_term`
- `INDICO_get_event_details`
- `INDICO_search_categories_by_id`
- `INDICO_get_event_contributions`
- `INDICO_get_files` — when called with `download_files: true`, writes attachments
  to the shared volume (see below). The default `download_dir` is patched in
  [`entrypoint.py`](entrypoint.py) so the data-manager can read them.

Paired with these is an archi-side agent tool, **`ingest_indico_event`**, that
finishes the round trip — see the next section.

## Ingesting event attachments

The MCP server alone fetches *metadata*. To make the *contents* of attachments
(slide text, agenda PDFs, supporting materials) searchable in the vectorstore,
the agent chains two tool calls:

```
INDICO_get_files(event_id, download_files=true)   # MCP: auth + download to volume
ingest_indico_event(event_id=…, event_url=…)      # archi: chunk + embed + index
```

The two halves are bridged by a docker named volume:

- The MCP sidecar mounts it RW at `/shared/indico-downloads` (env
  `INDICO_DOWNLOADS_DIR=/shared/indico-downloads`, declared in the Dockerfile and
  enforced by the wrapper in `entrypoint.py`).
- The data-manager mounts the same volume **read-only** at the same path. Its
  `POST /document_index/ingest_local_path` endpoint validates the path is under
  one of the allowlisted roots (default `/shared/indico-downloads`, override via
  the `INGEST_ALLOWED_ROOTS` env var on the data-manager) and walks the directory.

The compose template (`src/cli/templates/base-compose.yaml`) wires this up
automatically when an MCP server entry sets `shared_volume: <name>`:

- Declares a `<name>-<deployment>` named volume.
- Mounts it RW on the MCP sidecar at `/shared/<name>`.
- Mounts it RO on the data-manager at `/shared/<name>`.
- Sets `<NAME_UPPER>_DIR` env on the MCP sidecar (e.g. `INDICO_DOWNLOADS_DIR`).

After ingestion, the agent retrieves resources deterministically with
`search_metadata_index` using `event_id:<id>` (stamped into each resource's
metadata), then `fetch_catalog_document` by hash — *not* with
`search_vectorstore_hybrid`, which won't reliably rank a freshly-added event
near the top of a large corpus.

Notes & caveats:

- Upstream `INDICO_get_files` silently caps to ~10 attachments per event. Larger
  events lose files; the response surfaces a `Limited downloads to first N files`
  warning line — flag it to the user.
- `ingest_url` (the generic-URL ingest tool) refuses Indico event URLs explicitly
  and points the agent back to `ingest_indico_event`. Don't try to bypass it —
  the LinkScraper can't authenticate against CERN SSO and would store the login
  page.
- For database compatibility, the stored `documents.source_type` is `"web"`
  (the `valid_source` CHECK constraint doesn't allow `"indico"`); the actual
  scraper is recorded as `metadata.scraper="indico"`.

## Bumping the upstream pin

```sh
# get latest master SHA
curl -s 'https://gitlab.cern.ch/api/v4/projects/itgpt%2Fmcp4indico/repository/commits?ref_name=master&per_page=1' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])"

# update MCP4INDICO_REF in this directory's Dockerfile, rebuild, smoke-test
```
