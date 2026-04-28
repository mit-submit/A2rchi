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
- **Materials/slides attached to an event**: `INDICO_get_files` with `download_files: false`
  to list URLs and metadata. **Do not** pass `download_files: true` — those files land
  inside the MCP sidecar where archi can't read them.
- **Who am I / what perms do I have**: `INDICO_get_user_info`.

## Making slides searchable: pair with `ingest_url`

When the user wants the *contents* of an event (slide text, agenda body) — not just
metadata — chain `INDICO_get_files` with `ingest_url`:

1. `INDICO_get_files(event_id, download_files=false)` — confirm the event exists, get
   the canonical event URL.
2. `ingest_url(<event_url>)` — data-manager auto-routes Indico URLs through
   `IndicoScraper`, which pulls slides via the API, converts them to markdown, and
   indexes them in the vectorstore.
3. After `ingest_url` returns its resource count, answer the question with
   `search_vectorstore_hybrid`.

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
