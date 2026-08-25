# cern-team quickstart

Get an Archi-on-OKG instance running on a fresh machine, holding a GitHub
repository and a GitLab repository, with a chat frontend in front of it.

`cern-team-demo.md` is the evidence runbook for the same bundle — longer,
and written to prove things rather than to be followed.

**What was actually run.** Steps 1–7 were executed end to end on
2026-08-25 against an empty database, and the results are quoted inline.
Two things are **not** verified and are marked where they appear: the
TWiki source (§8) and the chat frontend (§9–10). Everything else in this
document was run.

Commands use `okg-venv/bin/...` explicitly rather than assuming an
activated environment.

---

## Before you start

**Postgres with okg's extensions — not any Postgres.** The instance needs
`btree_gist`, `fuzzystrmatch`, `pg_textsearch`, `pg_trgm`, `timescaledb`
and `vector`. Three of them — `timescaledb`, `pg_cron` and `pg_textsearch`
— must be listed in `shared_preload_libraries` at server start, which a
stock Postgres will not have. Use okg's own image
(`ops/pg/docker-compose.yaml` in the okg repo). Give each instance its own
database.

**Install both packages into one environment.** The `archi` wheel is not
on PyPI yet, so install it from a built artifact or the repository:

```bash
python3 -m venv okg-venv
okg-venv/bin/pip install -e /path/to/okg
okg-venv/bin/pip install /path/to/archi/python/dist/archi-*.whl
```

## 1. Point at the bundle and the database

```bash
export OKG_PROFILES_DIR="$(okg-venv/bin/archi-profiles-dir)"
export OKG_DEPLOYMENTS_DIR="$PWD/deployments"
export OKG_DSN='postgresql://USER:PW@HOST:PORT/DBNAME'
export ARCHI_DATA_ROOT="$PWD/deployments/myteam"
```

`OKG_PROFILES_DIR` is still required. The wheel carries the bundle and the
playbooks, so no checkout of this repository is needed — but okg finds
profiles by environment variable, current directory, its own library or a
fetch cache, never inside installed packages. Closing that is okg#1179.

`ARCHI_DATA_ROOT` matters: connector caches and repository clones are
anchored to it. Set it absolute, as above, and no later step depends on
which directory you are standing in.

## 2. Install the bundle

```bash
okg-venv/bin/okg install --profile cern-team \
  --deployment-name myteam \
  --postgres-dsn '${OKG_DSN}' \
  --no-publish \
  --github-repo-name click --github-repo-url https://github.com/pallets/click \
  --gitlab-repo-name ci-example \
  --gitlab-repo-url https://gitlab.cern.ch/gitlabci-examples/build_docker_image.git
```

Run it without the repository flags and it asks interactively instead;
leave a URL blank to install without that source.

**Quote `'${OKG_DSN}'` literally.** The manifest resolver substitutes it
at command time, so the password never lands in `deployment.yaml`.
Verified: the resulting manifest contains zero plaintext DSNs.

`--no-publish` matters — the schema step below must land before the first
catalog load.

**You do not clone anything.** The repository connectors own their
checkouts: given a URL they clone on first run and fast-forward after.

**Result:** deployment directory with `deployment.yaml`,
`source_registry.yaml`, `invariants.yaml`, `schemas/`, and 21 playbooks.

## 3. Copy the schema slices

Still manual (okg#1179): okg's installer materializes playbooks from the
bundle but has no channel for schema files, and the catalog load needs
them.

```bash
S=$(okg-venv/bin/python -c "import archi,os;print(os.path.dirname(archi.__file__))")/schemas
D=deployments/myteam/schemas
cp "$S/operations.yaml" "$S/sources.yaml" "$D/"
mkdir -p "$D/bridges" && cp "$S/bridges/"*.yaml "$D/bridges/"
```

Copy them **verbatim** — the shipped bridges compose on every consumer,
and hand-editing them is what W3's criterion used to fail on.

## 4. Extensions, migrate, role passwords

```bash
for e in btree_gist fuzzystrmatch pg_textsearch pg_trgm timescaledb vector; do
  psql "$OKG_DSN" -c "CREATE EXTENSION IF NOT EXISTS $e"
done
okg-venv/bin/okg migrate --deployment myteam --apply --json
psql "$OKG_DSN" -c "ALTER ROLE okg_mcp PASSWORD 'okg_mcp'"
psql "$OKG_DSN" -c "ALTER ROLE app_rw  PASSWORD 'okg_rw'"
psql "$OKG_DSN" -c "ALTER ROLE app_ro  PASSWORD 'okg_ro'"
```

One statement per `psql` call on purpose: `psql` abandons the rest of a
`-c` chain after the first error, silently leaving extensions missing.

The role passwords are the second manual step (okg#1179) — `migrate`
creates those accounts without passwords on a password-authenticated
server, and the catalog load fails until they are set.

## 5. Claim, load, check

```bash
okg-venv/bin/okg catalog ownership claim --deployment deployments/myteam --json
okg-venv/bin/okg catalog load --deployment deployments/myteam --apply --json
okg-venv/bin/okg deployment lint myteam --json
```

Claim ownership **after** the schemas and registry are final — the claim
records what it saw, and a later edit makes the next publish refuse.

**Result:** 42 subtypes, 184 narrowings, four modules composed; lint
`ok: true` with zero blockers and zero warnings. That lint is static
(`db_required: false`) — it checks the configuration, not the database.

## 6. Ingest and publish

```bash
okg-venv/bin/okg ingest --deployment myteam --json \
  --exclude docsite,jira,twiki_eos,twiki_crawl
```

**The exclusions are load-bearing, not tidiness.** The completeness gate
requires every *selected* source to finish, whether or not it is marked
optional — so one unconfigured source blocks the publish for all of them.
`docsite`, `jira` and `twiki_eos` read caches this quickstart never
builds; `twiki_crawl` needs the cookie from §8. Drop a name from that list
only once you have configured it.

**Result:** `completeness: complete`; `cmssw_releases`, `github_repo` and
`gitlab_repo` all completed; publish status `published`. The repositories
were cloned by the connectors during this step.

## 7. Confirm it worked, then look at it

```bash
okg-venv/bin/okg status --deployment myteam --json
okg-venv/bin/okg search --deployment myteam --query "docker image build"
```

Check `latest_published_status` is `published` and that
`latest_published_generation` is not null. If it is null, the ingest did
not publish — re-read the exclusions in §6.

**Result:** published generation present;
`cmssw_release: 2409`, `source_file: 174`, `git_branch: 2`; and the search
returned real files from the GitLab repository, pinned to that generation.

Direct SQL against the graph tables is refused by design — use these.

## 8. Adding the TWiki

> **Not verified.** The reasoning below is evidenced; the working
> configuration is not — no CERN SSO cookie was available on this host.

**A CERN SSO cookie is required even when the wiki page is public.** The
connector reads a topic's source through `?raw=all`, and a direct request
to that URL on a public CMS topic returns a redirect to `auth.cern.ch`,
while the rendered page returns 200 to an anonymous client. The connector
detects the login bounce and refuses to ingest it, which is correct — a
login page silently stored as wiki text is worse than a clean failure.

What was **not** demonstrated is the cookie path working. To try it: run
the CERN SSO login helper (it prompts for a code from your
authenticator), `export CERN_TWIKI_COOKIE_FILE=/path/to/cookies.txt`, drop
`twiki_crawl` from the §6 exclusions, and re-run the ingest.

Leave `twiki_max_depth` at `0`. At depth 1 the crawl follows every link on
the seed page, and one unreachable topic makes the run refuse to claim it
saw everything — correct, but fragile on a link-rich page.

## 9. Stand up the chat frontend

> **Not verified** — no model-provider key was available on this host.
> Flags below are taken from each command's own `--help`.

```bash
export OKG_CHAT_APP_DATABASE_URL='postgresql://...'   # the chat app's OWN database
export OKG_CHAT_WEBUI_SECRET_KEY='...'                # instance secret

okg-venv/bin/okg chat-instance up --deployment myteam \
  --container-runtime podman \
  --instance-port 8080
okg-venv/bin/okg chat-instance status --deployment myteam
```

`--deployment` is required. `--container-runtime` defaults to **docker**,
so a podman host must say so. `--instance-port` is effectively required
until the manifest can carry it — stand-up refuses rather than picking a
port. Both environment variables are named as *variable names* on purpose;
neither the manifest nor the command line ever carries the value.

Then declare a `chat:` block in `deployment.yaml` and project it:

```bash
export OKG_CHAT_INSTANCE_URL=...     # from `chat-instance status`
export OKG_CHAT_ADMIN_TOKEN=...      # the instance admin key
okg-venv/bin/okg chat sync --deployment myteam
```

`sync` writes the declaration into the instance and reads every change
back to prove it landed. The block's fields are validated strictly and an
unknown key is refused, so write it against
`okg/src/okg/substrate/deployments/chat_block.py` rather than from
memory. Leave the `ui:` sub-block out unless you need it — its keys are
checked against an artifact that lives in okg's repository, which a
pip-only install may not have.

## 10. Give it a model

Configure the provider in Open WebUI itself with your own API key. It is
deliberately not part of the deployment declaration, which carries no
credentials. Add the provider in the instance's admin settings, name the
model in `chat.models`, re-run `okg chat sync`.

---

## What still needs a human

1. **`OKG_PROFILES_DIR`** — okg cannot discover profiles inside an
   installed package (okg#1179).
2. **The schema copy and the role passwords** — same gap: the install
   contract has no channel for either.
3. **The TWiki cookie** — the SSO login is interactive by design.

Together these are why this is a ten-step document rather than one
command. okg's installer already provisions, migrates, loads and
publishes on its own; archi opts out of all of it because of item 1.
