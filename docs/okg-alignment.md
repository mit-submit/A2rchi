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

*Last updated 2026-08-24, archi branch `archi_v3` @ `81dbdb6b` (PRs #610, #611,
#616, #617 merged), tested against okg `dev` @ `f5ec3b58d` (2026-08-18).*

**Pin drift, flagged deliberately:** okg `dev` is now `03470e2b4` — **210 commits
ahead of the pin we have actually validated against**. Per this page's own
re-audit-on-bump rule that is an open exposure, not a claim of compatibility:
until we re-pin and re-run, treat every statement below as verified against
`f5ec3b58d` only. It matters more than usual while #1181 (the connector/enricher
SDK) is open, because we still import ten private `okg.substrate` symbols.

**Sync channel: okg#1178** (established 2026-08-19; surface-change heads-ups land
there). Re-pin validation 2026-08-19 against the strict registry-admission schema
(`8b65a330a`, #1273): all four scratch registry entries (bootstrap fixture,
cmssw-releases, jira, docsite) lint clean, ingest OK, and publish — **zero
refusals**; full test suite 199/199 on the new pin. okg#1006 is merged into `dev`
(2026-08-13): chat, MCP HTTP auth, and principal mapping are now landed premises —
Archi's W9 targets `dev`. **Upstream state re-verified 2026-08-24:** #1275 (the
chat/console slices) is **merged** — the remaining W9 dependency is its parent
#1183, still open. Of our three filed frictions, **#1283 (`output_scope_summary`
hand-duplication) is closed upstream**; #1282 (narrowings outside
`schemas/bridges/` silently ignored — the real bug of the three) and #1284 (no
`partial` SourceHealth status) remain open. Our import surface is CI-gated on the
okg side
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
(demo friction 6; repro in the runbook) — being triaged upstream as a separate
substrate correctness issue (#1178 response, 2026-08-21). The consolidated PR
(#610) is **merged**, and okg#1178 has pinned `archi_v3` @ `728739e6` as the first
real external-consumer baseline for the deployment-product work. Both manual
install steps now have upstream owners: the schema-slice copy + hand-pruned
operations bridge land in `package-and-migrate-archi-products` (digest-bound
schema/ontology/bridge assets), and role/credential provisioning lands in
`apply-and-operate-deployment-instances` (secret refs from the instance,
lifecycle-provisioned roles). The conformance harness will use `cern-team` as its
first real consumer; the final #1185 claim adds artifact-only checks (no
`OKG_PROFILES_DIR` or authoring checkout, no manual schema/password steps,
lock/release readback, defined no-change reapply). One question is open to us on
the channel: whether sealing the v3 wheel together with a materialized bundle +
playbook payload conflicts with our distribution boundary (maintainer's answer
pending).
Comp-ops instance model (maintainer, 2026-08-21): the instance repo is
`gitlab.cern.ch/archi/cms-compops` (branch `archi_v3`); `okg-deployments/cms` is the
frozen parity reference and **retires once the cms-compops v3 instance reaches
parity** (retirement is mitdbg's call — relevant to your packaging PACT's proof
targets). **W7's local half is done and evidence-gated** (PACT change
`w7-compops-instance`): the full v3 instance definition (deployment manifest,
21-connector registry, materialized schemas incl. a pruned operations bridge,
20 playbooks, relocated parity auditor, credentialed bring-up runbook,
`versions.lock` pinning okg `f5ec3b58d` + archi `728739e6`) lives in
`cms-compops@archi_v3`; local validation on a fresh DB: lint zero
blocker/warning, 13 connectors ingest OK (one live fetch), 8 credential-gated
connectors fail clean with no scope claims, published generation idempotent on
re-run (evidence: `docs/w7-local-validation.md` there). The credentialed live
bring-up + strict parity run on a CERN host is the remaining half
(`docs/bring-up.md`). New friction for this channel: PACT gates resolve the
graph-projection deployment from `OKG_PACT_GRAPH_DEPLOYMENT` (default
`okg-workspace`) and ignore `pact/project.yaml`'s
`graph_projection.deployment` — external repos need the env var exported or
gates refuse with an okg-workspace database-identity mismatch. New PACT v4 note
from the re-pin: `change.tier` is now mandatory (`missing_lifecycle_tier`
otherwise). The superseded v2 application has been removed from the archi_v3
line (W10 teardown, PR #611 merged 2026-08-21); v2 lives on `main` until
instance cutover.

**Circle-back adversarial review + fixes (2026-08-24, PACT change
`circleback-fixes`):** a four-domain adversarial review of `archi_v3` @
`728739e6` (your pinned external-consumer baseline) found 3 blockers / 15
majors, dominated by connectors claiming `completed_scope` after silently
losing input — a mass-retraction hazard under `missing_from_completed_scope`.
All confirmed blocker/major findings are fixed with per-finding regression
tests (suite 254 → 320); ~85% of the bugs exist identically in the frozen
canonical `okg-deployments/cms` copies (deviations recorded in the change
dir's notes files). Two items are routed to this channel instead of patched
locally: (1) **likely root cause for the deletion-semantics finding you are
triaging** — no adapter ever provides `SourceRun.record_set`, and the runner
then silently skips retraction synthesis rather than failing loudly; (2) the
enricher derived-edge lifecycle (attrs-independent dedupe keys block
re-derivation after any transient retraction; no evidence-based retraction) —
both substrate-contract questions. Also relevant to the sealed-artifact
design: bundle playbooks are symlinks escaping `bundles/`, so packaging must
dereference (your "materialized payload" wording already covers this).

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
| #1179 external schemas/bundles | **The wheel now carries the bundle and its materialised playbooks** (force-include; the repo's playbook symlinks are dereferenced at build), so an install needs no authoring checkout — `archi-profiles-dir` prints the installed payload path. What remains on your side: the profile resolver has **no installed-distribution discovery**, so `OKG_PROFILES_DIR` is still mandatory (cascade = explicit override → env var → `./profiles/` → substrate library → fetch cache, `profile_init.py`). Also still open: shared ontology *modules* (`load_modules_root()` is package-locked) and a versioned distribution contract | ADR §7 ask 2; deployment-scoped schema slices shipping in the wheel (`python/archi/schemas/`) |
| #1180 per-user MCP | Blocks multi-user instances (cms-knowledge-base bundle) | ADR D12 |
| #1181 connector/enricher SDK | Replaces the import surface above; please treat that list as the minimum viable SDK | W1 friction log `pact/changes/w1-prove-the-seam/FRICTION.md` (6 items: scaffold nomos gap, ownership-claim discoverability, role passwords, --json purity, path-vs-name resolution); wheel + scratch-deployment reproduction in the same dir |
| #1182 external review | Unblocks the queue-bot (Redmine/JIRA reply) — our W8, same first consumer you named. Approve-with-edits + expiry confirmed in your scope: exactly what we needed | ADR §5.3, §7 ask 3 |
| #1183 chat | Archi ships per-bundle chat config only | ADR W9 |
| #1184 automation accounting | Matches our kill criterion (defer until >1 real automation) | ADR §7 ask 4 |
| #1185 end-to-end proof | **Our `cern-team` bundle install demo is this proof** — an external, wheel-installed distribution driven end to end on a SubMIT host. Coordinate before building a synthetic consumer | W1 seam proof (done); demo lands with the bundle work |

**Packaged bridges cannot compose anywhere as shipped — a #1179 blocker with
two independent instances in our tree.** Copying the wheel's
`schemas/bridges/operations.yaml` verbatim fails `okg catalog load` with
`bridge_subtype_unknown`, because its narrowings lack
`optional_when_subtypes_missing` and therefore require every referenced subtype
to be in the composed module set. First failure, verbatim from the executed
runbook (`docs/cern-team-demo.md:106-110`): narrowing
`documentation_page_references_dataset`, child subtype `dataset`. Both
consumers hit it independently and both had to hand-prune: the cern-team demo
(friction 2, `docs/cern-team-demo.md:269-275`) and the comp-ops instance, whose
committed copy "prunes the five narrowings owned by the ticket/service/code
modules" (`cms-compops docs/bring-up.md:63-67`). **The fix already has
precedent in our own tree** — `schemas/bridges/sources.yaml` flags exactly this
case for the MONIT narrowings. Ask, unchanged from the demo's friction log:
packaged bridges should flag cross-module narrowings
`optional_when_subtypes_missing`, or ship split per family. Until then a
distribution cannot ship a bridge that composes on an arbitrary instance, which
also bears on the sealed-artifact packaging design — a materialised payload that
still needs hand-pruning at install is not artifact-only.

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
- Bugs we found in shared/canonical code: two TWiki parser bugs in
  `okg-deployments/cms/cms_sources/twiki_eos.py` (the `=code=` regex eats `=` from
  assignments across lines; title-less heading markers absorb the next line) —
  fixed on the Archi side with documented deviation
  (`pact/changes/w2-consolidate-hep-sources/v2-reconciliation-notes.md`) and
  **upstreamed into the canonical copy via mitdbg/okg-deployments#88**.
