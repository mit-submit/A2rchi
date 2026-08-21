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

*Last updated 2026-08-21, archi branch `w2-twiki` @ `f07022f8`, tested against okg
`dev` @ `f5ec3b58d` (2026-08-18).*

**Sync channel: okg#1178** (established 2026-08-19; surface-change heads-ups land
there). Re-pin validation 2026-08-19 against the strict registry-admission schema
(`8b65a330a`, #1273): all four scratch registry entries (bootstrap fixture,
cmssw-releases, jira, docsite) lint clean, ingest OK, and publish — **zero
refusals**; full test suite 199/199 on the new pin. okg#1006 is merged into `dev`
(2026-08-13): chat, MCP HTTP auth, and principal mapping are now landed premises —
Archi's W9 targets `dev`, and multi-user instances are unblocked pending #1183's
per-bundle chat surface (#1275). Our substrate frictions are now okg #1282 / #1283 /
#1284; our import surface is CI-gated on the okg side
(`tests/substrate/contracts/test_external_import_surface.py`). D11 (shared ontology
modules in the wheel) is planned against the deployment-product packaging wave
(digest-pinned distribution assets), not an env-var override.

**All eight W2 tasks are done and evidence-gated** (PACT change
`w2-consolidate-hep-sources`; suite 250/250): packaging, `archi.auth`, all 12
connector families (JIRA + docs ingest-proven live; TWiki EOS reader + crawler over
one parser core; MONIT; and the catalog/feed batch — CRIC, CRIC-core, CMSSW
releases, CondDB, DBS, DQM, GitHub repos, HyperNews, Indico, SITECONF, GOCDB,
WMStats — fixture-tested, registry templates in the ingest-proven strict shape),
4 live tools, 5 enrichers + packaged defaults + the anonymizer (anonymize_data
cutover gate closed), 18 playbooks, and the comp-ops lint re-check (zero
regressions vs baseline; sole delta a version-introduced skipped notice). Schema
slices: `operations.yaml` + `sources.yaml` (+ bridges), every class/narrowing
single-defined. **The cern-team bundle + end-to-end install demo are DONE and gated
(PACT w6-cern-team-bundle)** — `okg install --profile cern-team` on a fresh DB: lint
zero blocker/warning, four connectors ingest OK (live cmssw releases.map fetch, 314
nodes), generation `gen:20260821T155948019759Z:56ad3d3dafec` published with
cross-connector reference edges, idempotent re-run. **The reproducible runbook is
`docs/cern-team-demo.md` — this is the okg#1185 release-claim artifact**; run results
+ 7-item friction log in `pact/changes/w2-consolidate-hep-sources/`. For #1179: two
manual steps remain outside the profile contract (wheel schema-slice copy incl. a
pruned operations bridge; role passwords post-migrate) — both flagged in the runbook.
**New substrate finding for this channel: deletion semantics not enforced** — stale
records survived reconcile + `--reset-cursor` under `missing_from_completed_scope`
(demo friction 6; repro in the runbook). The consolidated PR to `archi_v3` is open.
The comp-ops instance (`okg-deployments/cms`) is untouched and is the parity target
for cutover. New PACT v4 note from the re-pin: `change.tier` is now mandatory
(`missing_lifecycle_tier` otherwise).

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
okg.substrate.enrichers.base:     EnrichResult, IncrementalContext
okg.substrate.enrichers.derived_edges:
    DerivedEdgeCandidate, insert_deterministic_edges, mint_edge_id
okg.substrate.library.linkers:    _chronos
okg.substrate.library.linkers.declarative: DeclarativeLinker
okg.substrate.alias.protocol:     AliasMatch
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
