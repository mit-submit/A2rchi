# cern-team quickstart

Get an Archi-on-OKG instance running on a fresh machine, holding a
GitHub repository, a GitLab repository and a TWiki, with a chat
frontend in front of it.

This is the short path. `cern-team-demo.md` is the evidence runbook for
the same bundle — longer, and written to prove things rather than to be
followed.

**What was actually run.** Everything through step 9 was executed end to
end on 2026-08-25 against a fresh database, and the results are quoted
inline. Steps 10–11 (the chat frontend) are **not verified here** — this
host has no model-provider key — so they are written from okg's command
contracts and marked. Nothing else in this document is untested prose.

---

## Before you start

- **Postgres with okg's extensions.** Not any Postgres: the instance
  needs `btree_gist`, `fuzzystrmatch`, `pg_textsearch`, `pg_trgm`,
  `timescaledb` and `vector`, and the last three must be preloaded at
  server start. Use okg's own image (`ops/pg/docker-compose.yaml` in the
  okg repo) — a stock Postgres will fail at step 5.
- **One database per instance.** Never point two instances at the same
  database.
- **Python 3.12+**, `git`, and a container runtime for the chat step.

Install both packages into one environment:

```bash
python3 -m venv okg-venv
okg-venv/bin/pip install -e /path/to/okg          # or from your fork
okg-venv/bin/pip install archi                     # the archi wheel
```

---

## 1. Point at the bundle and the database

```bash
export OKG_PROFILES_DIR=$(archi-profiles-dir)      # ships inside the wheel
export OKG_DEPLOYMENTS_DIR=$PWD/deployments
export OKG_DSN='postgresql://USER:PW@HOST:PORT/DBNAME'
```

`OKG_PROFILES_DIR` is still required. The wheel now carries the bundle
and the playbooks, so you no longer need a checkout of this repository —
but okg finds profiles by environment variable, current directory, its
own library or a fetch cache, and never by looking inside installed
packages. Removing that variable needs a change on the okg side
(okg#1179).

## 2. Install the bundle

```bash
okg install --profile cern-team \
  --deployment-name myteam \
  --postgres-dsn "$OKG_DSN" \
  --no-publish
```

It asks ten questions. The ones that matter here:

| Question | What to give it |
|---|---|
| `github_repo_root` | where you will clone the GitHub repo (step 4) |
| `gitlab_repo_root` | where you will clone the GitLab repo |
| `twiki_base_url` | e.g. `https://twiki.cern.ch/twiki` |
| `twiki_seed_topic` | `Web/Topic`, e.g. `CMSPublic/SWGuideCrab` |
| `twiki_max_depth` | leave at `0` — see step 6 |

`--no-publish` matters: two manual steps below have to land before the
first publish.

**Result:** deployment directory with `deployment.yaml`,
`source_registry.yaml`, `invariants.yaml`, `schemas/` and 21 playbooks.

## 3. Copy the schema slices

Still manual (okg#1179). Copy four files out of the installed wheel into
the instance:

```bash
S=$(python -c "import archi,os;print(os.path.dirname(archi.__file__))")/schemas
D=deployments/myteam/schemas
cp "$S/operations.yaml" "$S/sources.yaml" "$D/"
mkdir -p "$D/bridges" && cp "$S/bridges/"*.yaml "$D/bridges/"
```

Copy them **verbatim**. Earlier instances had to hand-prune the
operations bridge; that was fixed, and both files now compose as
shipped.

## 4. Clone the two repositories

The repository connectors read a working tree on disk — they do not
clone or fetch. Put the clones where step 2 said they would be:

```bash
git clone --depth 1 https://github.com/ORG/REPO   deployments/myteam/repos/github-repo
git clone --depth 1 https://gitlab.cern.ch/ORG/REPO.git deployments/myteam/repos/gitlab-repo
```

A public CERN GitLab project clones with no credential. A private one
needs your usual git credentials — the connector inherits them from the
clone, so nothing extra goes into the instance.

## 5. Create the extensions, then migrate

```bash
for e in btree_gist fuzzystrmatch pg_textsearch pg_trgm timescaledb vector; do
  psql "$OKG_DSN" -c "CREATE EXTENSION IF NOT EXISTS $e"
done
okg migrate --deployment myteam --apply --json
```

One statement per `psql` call on purpose: `psql` abandons the rest of a
`-c` chain after the first error, which silently leaves extensions
missing and makes `migrate` fail with a confusing message.

Then set the loopback role passwords — also still manual (okg#1179):

```bash
psql "$OKG_DSN" -c "ALTER ROLE okg_mcp PASSWORD 'okg_mcp'"
psql "$OKG_DSN" -c "ALTER ROLE app_rw  PASSWORD 'okg_rw'"
psql "$OKG_DSN" -c "ALTER ROLE app_ro  PASSWORD 'okg_ro'"
```

## 6. Get a TWiki cookie

**A CERN SSO cookie is required even when the wiki page is public.** The
connector reads a topic's source through `?raw=all`, and CERN redirects
that to its login page even where the rendered page returns fine to an
anonymous browser. The connector notices the login bounce and refuses to
ingest it — which is correct; a login page silently stored as wiki text
would be worse than a clean failure.

Run the CERN SSO login helper (it prompts for a code from your
authenticator), then:

```bash
export CERN_TWIKI_COOKIE_FILE=/path/to/cookies.txt
```

Skip this and the other sources still work — TWiki alone fails cleanly.

Leave `twiki_max_depth` at `0` to start. At depth 1 the crawl follows
every link on the seed page, and a single unreachable topic makes the
whole run refuse to claim it saw everything — correct, but fragile on a
link-rich page.

## 7. Claim, load, check

```bash
okg catalog ownership claim --deployment deployments/myteam --json
okg catalog load --deployment deployments/myteam --apply --json
okg deployment lint myteam --json
```

Claim ownership **after** the schemas and registry are final — the claim
records what it saw, and a later edit makes the next publish refuse.

**Result:** 42 subtypes, 184 narrowings, four modules composed, and lint
`ok: true` with zero blockers and zero warnings.

## 8. Ingest

```bash
cd deployments/myteam
okg ingest --deployment myteam --progress --json \
  --exclude docsite,jira,twiki_eos
```

The exclusions are the bundle's other connectors, which read caches this
quickstart never builds. Leaving them in blocks the publish: the
completeness gate requires every *selected* source to finish, whether or
not it is marked optional.

**Result:** `cmssw_releases`, `github_repo` and `gitlab_repo` all
completed. With the cookie from step 6, `twiki_crawl` joins them; without
it, it fails cleanly and claims no scope.

## 9. Look at what you built

```bash
okg status --deployment myteam --json
okg search --deployment myteam --query "your question here"
okg trace node <node-id> --deployment myteam
```

Direct SQL against the graph tables is refused by design — use these.

---

## 10. Stand up the chat frontend

> **Not verified on this host** — no model-provider key was available.
> The commands are okg's; the sequence is not proven end to end.

```bash
okg chat-instance up
okg chat-instance status
```

`up` starts a pinned Open WebUI and proves it answered. Then declare a
`chat:` block in `deployment.yaml` — site name, which models to offer,
the system prompt, starter questions, and which graph tools to expose —
and project it:

```bash
export OKG_CHAT_INSTANCE_URL=...      # from `chat-instance status`
export OKG_CHAT_ADMIN_TOKEN=...       # the instance admin key
okg chat sync
```

`sync` writes the declaration into the instance and reads every change
back to prove it landed.

Keep the `ui:` sub-block out unless you need it — its keys are validated
against an artifact that lives in okg's repository, so a pip-only
install may not have it. Everything else in the block works without it.

## 11. Give it a model

The model provider is configured in Open WebUI itself with your own API
key — it is deliberately not part of the deployment declaration, which
carries no credentials. Add the provider in the chat instance's admin
settings, pick the model in `chat.models`, re-run `okg chat sync`.

---

## What still needs a human

Three things in this document cannot be automated from here, and all
three are okg-side rather than ours:

1. **`OKG_PROFILES_DIR`** — okg has no way to discover profiles inside an
   installed package (okg#1179).
2. **The schema copy and the role passwords** — same issue: no channel in
   the install contract for either.
3. **The TWiki cookie** — the SSO login is interactive by design.
