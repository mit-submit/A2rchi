# CMS OKG Traversal Recipes

Use this skill when the agent needs to decide which CMS OKG MCP tool to call
next.

## Tool Selection

- `inspect(target={kind: "graph"})`: get the graph shape when unsure which
  subtypes or edge types exist.
- `inspect(target={kind: "subtype", subtype: ...})`: inspect fields and edge routes for `site`,
  `storage_endpoint`, `jira_issue`, `documentation_page`, `document_chunk`,
  `dataset`, `workflow`, and `monitoring_snapshot`.
- `search`: discover candidate nodes. Use short queries, exact CMS identifiers,
  and alternate terms rather than one long keyword dump.
- `inspect(target={kind: "node", node_id: ...})`: inspect canonical candidates
  and read `skill_hints`.
- `expand` / `aggregate`: expand a candidate node, roll
  chunks to parents, and understand adjacent evidence volume before taking many
  rows.
- `expand(input={node_id: ...}, path=[...])`: follow known edge routes once the
  start node and edge type are clear.
- `query`: use only bounded read-only `SELECT` or `WITH` queries over
  `okg.v_*` views for counts, impact views, or checks that are clearer as SQL.
- `search(method="identifier_mentions")`: extract exact identifiers from
  question text or evidence snippets when search terms are ambiguous.

## Query Refinement

- Try literal identifiers first: JIRA keys, site names, RSE names, datasets,
  workflow names, service names, release strings, and command fragments.
- If results are weak, broaden by removing overly specific terms, then narrow
  with exact IDs found in the first pass.
- Search by historical aliases for sites, endpoints, storage elements, and
  services.
- Prefer parent/source nodes for final citations. Keep chunks for exact prose
  support.

## Minimum Evidence

An evidence-backed answer should normally have:

- at least two OKG calls;
- at least one canonical ID when a canonical parent/source exists;
- at least one expansion call after first evidence is found;
- a gap statement when only chunk-level or conflicting evidence is available.

## Live-State Completion

When a question asks what is happening now, classify the live authority before
answering:

- CRAB task questions: CRAB task metadata/state and scheduler/Condor evidence.
- Production workflow questions: ReqMgr2, Unified, WMAgent, job reports, and
  monitoring snapshots.
- Site/job failure questions: Condor or monitoring snapshots first, then site
  tickets and docs for explanation.
- Storage quota/replica questions: Rucio account/RSE/rule/replica state first,
  then policy docs and tickets.
- Ticket-status questions: current JIRA status and updated comments.

If that authority is not present in the pinned OKG generation, state that gap
and give the live checks an operator should run. The answer should include
decision criteria, not just a missing-source note.
