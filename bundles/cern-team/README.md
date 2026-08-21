# cern-team bundle

A generic CERN team knowledge instance, installable with the okg
profile installer. It composes:

- **Connectors** (source-defaults/): `twiki_eos` (EOS-snapshot TWiki
  reader), `docsite` (documentation-site records cache), `jira`
  (issue records cache, credential by env-var reference only),
  `cmssw_releases` (live public cms-bot releases.map fetch, no auth).
  `indico` ships as an optional template
  (`source-defaults/indico.yaml.example`); code repositories are
  wired through the substrate's own okg modules (git_graph /
  code_graph) when an instance opts in after install.
- **Modules** (modules.yaml): `document_starter`, `person`,
  `extraction` — the trio proven by the w1-scratch instance
  (pact `w2-consolidate-hep-sources`, INGEST-PROVEN 2026-08-11/12).
- **Playbooks** (skills/): the archi playbook set, symlinked from the
  repo top-level `skills/` directory (single source of truth; the
  installer reads through symlinks and copies file contents verbatim
  into `<deployment>/skills/`). `deployment-defaults.yaml` wires them
  in via `skills_dir` + `skill_triggers`.
- **Invariant** (invariants.yaml): core pages floor —
  `documentation_page` / `jira_issue` / `cmssw_release` must each
  have at least one live row after the first ingest.
- **Manifest defaults** (deployment-defaults.yaml): runtime worker
  off, and the `nomos.rollout` deferred block (owner
  `archi-cern-team`, review_by 2026-12-01) — the installer's
  deployment-defaults merge injects it, no manual step needed.

Terminology: *bundle* (this directory) installs an *instance* (a
deployment directory + database) whose *connectors* ingest and whose
*playbooks* guide agents.

No file in this bundle carries an operator path or a credential
value: connector paths are `${archi_data_root}`-relative and
credentials are env-var references (`credential_refs`).

## Install runbook

The complete, executed version (every command with expected output)
is `docs/cern-team-demo.md` at the repo top level. Summary:

```bash
# 0. Prerequisites: okg venv with the archi wheel installed; a
#    Postgres reachable by DSN; an empty database for the instance.
export OKG_PROFILES_DIR=<archi repo>/bundles
export OKG_DEPLOYMENTS_DIR=<your deployments root>
export OKG_DSN=postgresql://<user>:<password>@<host>:<port>/<db>

# 1. Scaffold the instance (no first publish; two post-install steps
#    must land before the first catalog load).
okg install --profile cern-team \
  --deployment-name <slug> \
  --postgres-dsn '${OKG_DSN}' \
  --non-interactive --no-publish

# 2. MANUAL STEP (okg#1179 contract input): copy the archi wheel's
#    schema slices into the instance. The bundle cannot ship them —
#    the profile installer has no schema-asset channel, and the
#    subtypes (jira_issue, documentation_page, software_repository,
#    cmssw_release, ...) live in the wheel, not in any substrate
#    library module.
SITE=$(python -c 'import archi, pathlib; print(pathlib.Path(archi.__file__).parent)')
DEP=$OKG_DEPLOYMENTS_DIR/<slug>
cp "$SITE"/schemas/operations.yaml "$SITE"/schemas/sources.yaml "$DEP"/schemas/
mkdir -p "$DEP"/schemas/bridges
cp "$SITE"/schemas/bridges/sources.yaml "$DEP"/schemas/bridges/sources.yaml
# Do NOT copy schemas/bridges/operations.yaml verbatim: it references
# subtypes owned by substrate modules this bundle does not compose
# (dataset, ticket, service, repo_starter, git_graph) and its
# narrowings lack optional_when_subtypes_missing, so catalog load
# fails with bridge_subtype_unknown. Write a pruned
# "$DEP"/schemas/bridges/operations.yaml containing only the
# narrowings your connectors need — for this bundle exactly one:
#   cmssw_release_supersedes_release
#     (edge_archetype supersedes, CMSSWRelease -> CMSSWRelease)
# (see docs/cern-team-demo.md for the full file). Also: narrowings
# outside schemas/bridges/ are silently ignored and fail only at
# ingest (ProducerPolicyViolation) — keep the bridges/ split.

# 3. Materialize connector data under the instance's archi_data_root
#    (default: <deployment dir>/data): jira/records.json +
#    jira/meta.json, docsite/records.json, twiki-snapshot/*.txt
#    (topics of web_root sit at the snapshot ROOT; subdirs are
#    subwebs). cmssw_releases fetches its releases.map live on the
#    first ingest.

# 4. Bring the instance up. Relative data roots resolve against
#    ARCHI_DATA_ROOT (or the current dir) — anchor it explicitly:
export ARCHI_DATA_ROOT=$DEP
#    If the database is fresh on the bundled okg Postgres stack,
#    install the required extensions first (they only exist in the
#    stack's initial database):
psql "$OKG_DSN" -c "CREATE EXTENSION IF NOT EXISTS btree_gist" \
  -c "CREATE EXTENSION IF NOT EXISTS fuzzystrmatch" \
  -c "CREATE EXTENSION IF NOT EXISTS pg_textsearch" \
  -c "CREATE EXTENSION IF NOT EXISTS pg_trgm" \
  -c "CREATE EXTENSION IF NOT EXISTS timescaledb" \
  -c "CREATE EXTENSION IF NOT EXISTS vector"
okg migrate --deployment <slug> --apply
#    MANUAL STEP (known friction, okg#1179 input): okg migrate
#    creates the okg_mcp/app_rw/app_ro roles without passwords when
#    the server is password-authenticated; set them per okg
#    local_database_roles.py (loopback-only roles, fixed passwords):
psql "$OKG_DSN" -c "ALTER ROLE okg_mcp PASSWORD 'okg_mcp'" \
                -c "ALTER ROLE app_rw  PASSWORD 'okg_rw'" \
                -c "ALTER ROLE app_ro  PASSWORD 'okg_ro'"
#    Claim ownership only after schemas/ and source_registry.yaml
#    have their final content — the claim records the manifest
#    identity, and a later edit makes the publish refuse with
#    catalog_ownership_mismatch (recovery: re-run the claim).
okg catalog ownership claim --deployment "$DEP"
okg catalog load --deployment "$DEP" --apply
okg deployment lint <slug> --json     # expect zero blocker/warning
okg ingest --deployment <slug> --progress

# 5. Read-back proof.
okg search --deployment <slug> --query "<something in your corpus>"
```

## Known manual steps (each is okg#1179 contract input)

1. **Schema-slice copy from the wheel, with a pruned operations
   bridge** (step 2 above). The profile installer copies
   invariants/dashboards/skills but has no channel for deployment
   schema files; until profiles can ship `schemas/` assets, every
   archi bundle install carries this copy step. The wheel's
   `bridges/operations.yaml` additionally cannot be copied verbatim
   into this bundle's minimal module composition (see step 2) — the
   packaged bridge should either flag cross-module narrowings
   `optional_when_subtypes_missing` or be split per source family.
2. **Database role passwords after migrate** (step 4 above). The
   substrate's role provisioning does not set passwords on
   `okg_mcp`/`app_rw`/`app_ro` for password-authenticated servers;
   `okg catalog load` then fails on those roles until the ALTER ROLE
   statements run.

Resolved since the task was drafted: the `nomos.rollout` deferred
block needs **no** manual step — `deployment-defaults.yaml` injects it
through the installer's deployment-defaults merge.

## Notes

- The skills/ entries are symlinks to the repo's top-level `skills/`
  (one source of truth, no duplication). If this bundle is ever
  distributed outside a repo checkout (tar/fetch cache), materialize
  the files at packaging time — that packaging step is also #1179
  input.
- The playbook set follows the repo's `skills/` directory as it
  grows (e.g. `indico.md` landed with the Indico connector port).
- Known substrate limitation observed during the assembly demo
  (recorded in the w2 pact evidence): records that disappear from a
  connector's completed scope (e.g. a renamed twiki snapshot file)
  are NOT retracted on later ingests — `okg ingest --mode reconcile
  --reset-cursor` still leaves the stale facts live despite
  `deletion_semantics: missing_from_completed_scope`. Until that is
  fixed, treat identity-changing edits of connector data as a
  rebuild (fresh database) rather than a reconcile.
