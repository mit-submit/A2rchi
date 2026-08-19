# Archi ↔ OKG alignment page

**Audience:** anyone (human or agent) working on mitdbg/okg#1178 and its sub-issues.
This is the one page to read to stay in sync with the Archi side. It is versioned in
this repo; the copy on the `archi_v3` branch is canonical once merged, the `w2-twiki`
branch carries the freshest state between merges.

**Program documents:** the Archi program spec is
[`docs/adr/0001-archi-v3-program-spec.md`](adr/0001-archi-v3-program-spec.md) (this
repo). okg#1178 is the OKG-side program; the two describe the same three-layer
architecture. Terminology follows #1178: *distribution / bundle / instance /
connector / enricher / playbook / live tool / automation*.

## What Archi is, in one line

Archi is the HEP **distribution** installed on OKG: a pip package (`python/` in this
repo, wheel name `archi`) shipping connectors, enrichers, live tools, schemas,
playbooks, and bundles — no credentials, no site config, no running services, no
reimplementation of OKG services.

## Current state (update this section when it changes)

*Last updated 2026-08-18, archi branch `w2-twiki` @ `21273ff7`, tested against okg
`dev` @ `21c5b8c3e` (2026-08-11).*

Done, evidence-gated (PACT change `w2-consolidate-hep-sources` in `pact/changes/`):
packaging (wheel from `python/`), `archi.auth` (CERN preflight/cache/cookies),
connectors for JIRA + docs families (ingest-proven on a live scratch instance),
TWiki (EOS reader + live crawler over one parser core), MONIT (4 connectors + 4
live tools), 18 playbooks. In progress: remaining catalog connectors
(branch `wip-w2-catalogs`), enrichers, the `cern-team` bundle, the end-to-end
install demo. The comp-ops instance (`okg-deployments/cms`) is untouched and is
the parity target for cutover.

## The exact substrate surface Archi consumes today

This is the de-facto interface #1181 (public connector/enricher SDK) will replace.
If you change any of these on `dev`, Archi breaks; when the SDK lands, Archi
migrates to it in one sweep (imports are centralized).

**Python imports (all of them):**
```
okg.substrate.library.sources.base:
    NodeFact, EdgeFact, SourceRun, SourceHealth, SourcePreflightResult,
    ProgressMarker
okg.substrate.library.sources.content_hash_probe: ContentHashProbe
okg.substrate.library.sources.mutable_api_probe:  MutableApiProbe
okg.substrate.sources.preflight:
    file_ref_preflight, credential_env_preflight, http_probe_result
okg.substrate.sources.redaction:  redact_text
```

**Contracts consumed as data/CLI (not imports):** the source-registry entry schema
(`source_class`, `record_identity_*`, `change_probe_kind` soundness, admission
policy with `output_signature` + `output_scope_summary`, `sync:` block); deployment
manifest keys (`modules:`, `schema_dir`, `nomos.rollout` deferral); the
edit → `catalog ownership claim` → `catalog load --apply` → `ingest` sequence;
`okg deployment lint` codes; LinkML deployment schemas + `schemas/bridges/`
narrowings; install profiles (`OKG_PROFILES_DIR` cascade) for bundles; the closed
`SourceHealth`/preflight status vocabulary; MCP read surface for validation.

## Mapping: #1178 sub-issues ↔ Archi needs and evidence

| OKG issue | Archi's stake | Evidence we already have for you |
|---|---|---|
| #1179 external schemas/bundles | Bundles ship as install profiles today (works via `OKG_PROFILES_DIR`); the gap is shared ontology *modules* (`load_modules_root()` is package-locked) and a versioned distribution contract | ADR §7 ask 2; deployment-scoped schema slices shipping in the wheel (`python/archi/schemas/`) |
| #1180 per-user MCP | Blocks multi-user instances (cms-knowledge-base bundle) | ADR D12 |
| #1181 connector/enricher SDK | Replaces the import surface above; please treat that list as the minimum viable SDK | W1 friction log `pact/changes/w1-prove-the-seam/FRICTION.md` (6 items: scaffold nomos gap, ownership-claim discoverability, role passwords, --json purity, path-vs-name resolution); wheel + scratch-deployment reproduction in the same dir |
| #1182 external review | Unblocks the queue-bot (Redmine/JIRA reply) — our W8, same first consumer you named. Approve-with-edits + expiry confirmed in your scope: exactly what we needed | ADR §5.3, §7 ask 3 |
| #1183 chat | Archi ships per-bundle chat config only | ADR W9 |
| #1184 automation accounting | Matches our kill criterion (defer until >1 real automation) | ADR §7 ask 4 |
| #1185 end-to-end proof | **Our `cern-team` bundle install demo is this proof** — an external, wheel-installed distribution driven end to end on a SubMIT host. Coordinate before building a synthetic consumer | W1 seam proof (done); demo lands with the bundle work |

Additional substrate friction found while porting, not yet in any issue:
narrowings outside `schemas/bridges/` silently ignored (lint green, ingest-time
ProducerPolicyViolation); `output_scope_summary` must hand-duplicate
`output_signature`; no `partial` status in the closed SourceHealth vocabulary.
Details + repros: `okg-asks-drafts.md` items 6–7 in the archi_v3 working notes and
`pact/changes/w2-consolidate-hep-sources/parity-ingest-demonstration.md`.

## How to sync with us

- Read this page + the PACT change dirs under `pact/changes/` (porting matrix,
  ingest demonstrations, reconciliation notes — every deviation from the canonical
  cms code is recorded there).
- Interface drift: we pin the okg `dev` commit we tested against at the top of this
  page and re-audit on bump; if you change something in the consumed surface above,
  a heads-up on okg#1178 referencing this page is enough.
- Bugs we found in shared/canonical code that you may want upstream: two TWiki
  parser bugs present in `okg-deployments/cms/cms_sources/twiki_eos.py` (the
  `=code=` regex eats `=` from assignments across lines; title-less heading markers
  absorb the next line) — fixed on the Archi side with documented deviation, see
  `pact/changes/w2-consolidate-hep-sources/v2-reconciliation-notes.md`.
