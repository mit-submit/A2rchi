# Indico MCP server — domain knowledge

You have access to a set of `INDICO_*` tools backed by [Indico](https://indico.cern.ch),
CERN's event-management system. Use them for **live, precise lookups** about specific
events, contributions, categories, or attached materials.

## Tool selection

- **Event by ID** (e.g. user mentions a number like `137346` or pastes an Indico URL):
  `INDICO_get_event_details`. Pass `include_contributions: true` only when the user wants
  the agenda; otherwise the response is much shorter without it.
- **Search by free-text title/keyword across all categories**: `INDICO_search_events_by_term`.
- **List events under a known category** (e.g. weekly meetings): `INDICO_search_events_by_category_id`.
  If you don't know the category ID, walk the tree with `INDICO_search_categories_by_id`
  starting from `"0"` (root) — don't guess IDs.
- **Talks/contributions inside an event**: `INDICO_get_event_contributions`. Set
  `include_subcontributions: true` for sessions broken into sub-talks.
- **Materials/slides attached to an event** — two modes:
  - **List only** (default): `INDICO_get_files(event_id, download_files=false)`. Returns
    filenames, sizes, and API paths. Use this when the user just wants to know *what's
    attached* to an event.
  - **Download + ingest** (for "what's in the slides" questions): see the next section.
- **Who am I / what perms do I have**: `INDICO_get_user_info`.

## Making slides searchable: pair with `ingest_indico_event`

When the user wants the *contents* of an event (slide text, agenda body) — not just
metadata — use the authenticated MCP path. **Do NOT** use `ingest_url` on Indico URLs
(it cannot authenticate against CERN SSO and would store the login redirect page;
the tool will refuse and point you back here).

1. `INDICO_get_files(event_id, download_files=true)` — the MCP server authenticates
   with CERN, downloads each attachment to a shared volume (`/shared/indico-downloads/<event_id>/`),
   and reports per-file status. This is the only step that hits the network.
2. `ingest_indico_event(event_id=<id>, event_url=<canonical Indico URL>)` — asks the
   data-manager to chunk + embed + index everything that just landed in the shared
   dir. Stamps `event_id`, `url`, and `scraper=indico` into resource metadata.
3. Retrieve DETERMINISTICALLY with `search_metadata_index` using `event_id:<id>`,
   then `fetch_catalog_document` by hash. Do **not** loop on `search_vectorstore_hybrid`
   — exact-match metadata lookups always surface a freshly-ingested event; vector
   similarity does not.

Limit: upstream `INDICO_get_files` truncates to ~10 attachments per event. If a
large event is missing pieces, the response will say so — surface that to the user.

## Indico URL shape

URLs look like `https://indico.cern.ch/event/<event_id>/` and
`https://indico.cern.ch/category/<category_id>/`. Extract the integer ID from the path
when the user pastes a URL — don't ask them to repeat it.

## When NOT to call these tools

- Questions about general physics topics, deadlines, slack threads, or repo code: this
  server is *only* for Indico.
- Broad historical queries like "what did the SUSY group discuss last year" — that's
  better served by the vectorstore, which already has scraped slide content indexed.
  Only fall through to `INDICO_search_events_by_term` if vectorstore results are sparse.

## Errors

If a tool returns "❌ Please configure the Indico connection first", the sidecar's env
vars (`BEARER_TOKEN` / `API_KEY` / `API_SECRET`) are missing — surface this to the user
rather than retrying.
