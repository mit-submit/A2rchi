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
| Best for | "what was discussed at SUSY '24" — semantic recall over many past meetings | "list contributions for event 137346" — exact, current lookups |
| Freshness | Whenever the scraper last ran | Now |
| Cost per use | Cheap (one similarity search) | One Indico API call + LLM tool roundtrip per question |

They are complementary — keep both.

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
- `INDICO_get_files`

## Bumping the upstream pin

```sh
# get latest master SHA
curl -s 'https://gitlab.cern.ch/api/v4/projects/itgpt%2Fmcp4indico/repository/commits?ref_name=master&per_page=1' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['id'])"

# update MCP4INDICO_REF in this directory's Dockerfile, rebuild, smoke-test
```
