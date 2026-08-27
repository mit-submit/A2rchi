# cern-team quickstart

Get an Archi-on-OKG instance running on a fresh machine, holding a GitHub
repository and a GitLab repository, with a chat frontend in front of it.

`cern-team-demo.md` is the evidence runbook for the same bundle — longer,
and written to prove things rather than to be followed.

**What was actually run.** §1–3 were executed end to end on 2026-08-27
against an empty database, and the results are quoted inline. Two things
are **not** verified and say so where they appear: the TWiki source (§5)
and the chat frontend (§6–7).

---

## Before you start

**Postgres with okg's extensions — not any Postgres.** Three of the six
(`timescaledb`, `pg_cron`, `pg_textsearch`) must be in
`shared_preload_libraries` at server start, which a stock Postgres will
not have. Use okg's own image (`ops/pg/docker-compose.yaml` in the okg
repo). Give each instance its own database. The install creates the
extensions itself once the server can load them.

**Neither package can be installed from PyPI today.** Read this before
anything else — it is the step most likely to stop you.

- **okg is a private repository and is not published.** There is no
  `pip install okg`. You need read access to `mitdbg/okg`, then clone it
  and install from your clone. Without that access you cannot complete
  this document at all; ask the OKG maintainers.
- **`pip install archi` installs someone else's package.** The name on
  PyPI belongs to an unrelated archive library (`archi` 3.8.7.0,
  "Multi-format archive library based on libarchive"). Ours is
  `3.0.0a1` and is not published. Install it from the built wheel.

```bash
python3.12 -m venv okg-venv        # 3.12+ required; archi refuses older

# okg: private, so this needs your GitHub access. Clone, then install.
git clone git@github.com:mitdbg/okg.git
okg-venv/bin/pip install ./okg     # add -e only to edit okg itself

# archi: public, so pip fetches it directly — no clone, no wheel build.
okg-venv/bin/pip install \
  "archi @ git+https://github.com/archi-physics/archi@archi_v3#subdirectory=python"
```

Verified 2026-08-27: that second command installs into a clean 3.12
virtualenv and leaves `archi-profiles-dir` on the path, pointing at the
bundle inside the installed package. On Python 3.11 or older pip refuses
with *"Package 'archi' requires a different Python"*.

`-e` is an *editable* install: pip points at your clone instead of copying
it, so local edits take effect immediately. That is what a developer
wants; a team installing to use it should leave `-e` off.

## 1. Install

```bash
export OKG_PROFILES_DIR="$(okg-venv/bin/archi-profiles-dir)"
export OKG_DEPLOYMENTS_DIR="$PWD/deployments"
export ARCHI_DATA_ROOT="$PWD/deployments/myteam"

okg-venv/bin/okg install --profile cern-team \
  --deployment-name myteam \
  --postgres-dsn 'postgresql://USER:PW@HOST:PORT/DBNAME' \
  --github-repo-name click --github-repo-url https://github.com/pallets/click \
  --gitlab-repo-name ci-example \
  --gitlab-repo-url https://gitlab.cern.ch/gitlabci-examples/build_docker_image.git
```

**That is the install.** It creates the extensions, migrates, materializes
the distribution's schemas, loads the catalog, clones both repositories,
ingests, and publishes.

**You never run `git clone`** — the repository connectors own their
checkouts, cloning on first run and fast-forwarding after.

**But do give both repository URLs, or remove those two defaults first.**
Tested 2026-08-27: an install with no source flags at all does *not* reach
a published generation. `cmssw_releases` completes, but `github_repo` and
`gitlab_repo` are still selected with no URL, fail, and the completeness
gate blocks the publish for everything. To install genuinely empty, rename
`github_repo.yaml` and `gitlab_repo.yaml` to `.yaml.example` first; then
only the CMSSW catalog runs, and it needs no credential.

That is a gap, not a preference: ADR 0001 W6 requires an instance with
nothing configured to start cleanly, and a connector whose input is blank
should opt itself out rather than fail. Adding sources later is §3.

`ARCHI_DATA_ROOT` is worth setting absolute: connector caches and
repository clones anchor to it, so no later command depends on which
directory you are standing in.

**Result:** `first publish complete`, generation
`gen:20260827T142617739260Z:5e3c0fc5ada0`, **2,586 nodes / 2,279 edges**.

## 2. Confirm it worked

```bash
okg-venv/bin/okg status --deployment myteam --json
okg-venv/bin/okg search --deployment myteam --query "docker image build"
```

Check `latest_published_status` is `published`. The search returns real
files from the GitLab repository, pinned to that generation. Direct SQL
against the graph tables is refused by design — use these.

## 3. Add more sources

Three connectors are selected by default — the CMSSW release catalog and
the two repositories — because none of them needs a credential, so a bare
install reaches a published generation on its own.

Everything else ships as `.yaml.example` in the bundle's
`source-defaults/`: JIRA, documentation sites, Indico, and both TWiki
readers. To enable one, rename it to `.yaml`, supply its credential by
environment variable, and re-run the ingest:

```bash
okg-venv/bin/okg ingest --deployment myteam --progress
```

To add something the bundle does not ship — another repository, say —
there is `okg add <source> --deployment myteam` (`git-files` is the
repository one). It writes the registry entry but deliberately leaves it
incomplete, reporting `activation_blocked` and naming what is missing,
rather than activating a source it cannot fully describe. You finish the
entry in `source_registry.yaml`, then re-claim and re-load. Workable, but
hand-work — adding a source from a UI is OKG's operator console and is not
built yet.

**One rule worth knowing before you do.** The completeness gate requires
every *selected* source to finish, whether or not it is marked optional.
So a source you enable but cannot feed does not fail alone — it blocks the
publish for all of them. Enable one at a time and check it ingests.

## 4. Two steps the install still cannot do

Both are okg-side and both are being tracked:

- **Role passwords.** On a password-authenticated server the migration
  creates the loopback roles without passwords. If the catalog load fails
  on authentication, set them:
  ```bash
  psql "$OKG_DSN" -c "ALTER ROLE okg_mcp PASSWORD 'okg_mcp'"
  psql "$OKG_DSN" -c "ALTER ROLE app_rw  PASSWORD 'okg_rw'"
  psql "$OKG_DSN" -c "ALTER ROLE app_ro  PASSWORD 'okg_ro'"
  ```
- **`OKG_PROFILES_DIR`.** okg cannot discover profiles inside an installed
  package, so the variable is still needed even though the wheel carries
  the bundle (okg#1179).

## 5. Adding the TWiki

> **Not verified.** The reasoning is evidenced; the working configuration
> is not — no CERN SSO cookie was available on this host.

**A CERN SSO cookie is required even when the wiki page is public.** The
connector reads a topic's source through `?raw=all`, and a direct request
to that URL on a public CMS topic returns a redirect to `auth.cern.ch`,
while the rendered page returns 200 anonymously. The connector detects the
login bounce and refuses to ingest it — correctly; a login page silently
stored as wiki text is worse than a clean failure.

To try it: run the CERN SSO login helper (it prompts for a code from your
authenticator), `export CERN_TWIKI_COOKIE_FILE=/path/to/cookies.txt`,
rename `twiki_crawl.yaml.example`, and re-run the ingest. Leave
`twiki_max_depth` at `0` — at depth 1 the crawl follows every link on the
seed page, and one unreachable topic makes the run refuse to claim it saw
everything.

## 6. Stand up the chat frontend

> **Not verified** — no model-provider key was available on this host.
> Flags below are taken from each command's own `--help`.

```bash
export OKG_CHAT_APP_DATABASE_URL='postgresql://...'   # the chat app's OWN database
export OKG_CHAT_WEBUI_SECRET_KEY='...'                # instance secret

okg-venv/bin/okg chat-instance up --deployment myteam \
  --container-runtime podman --instance-port 8080
okg-venv/bin/okg chat-instance status --deployment myteam
```

`--container-runtime` defaults to **docker**, so a podman host must say
so. `--instance-port` is effectively required until the manifest can carry
it. Both environment variables are named as *variable names* on purpose —
neither the manifest nor the command line carries the value.

Then declare a `chat:` block in `deployment.yaml` and project it:

```bash
export OKG_CHAT_INSTANCE_URL=...     # from `chat-instance status`
export OKG_CHAT_ADMIN_TOKEN=...      # the instance admin key
okg-venv/bin/okg chat sync --deployment myteam
```

`sync` writes the declaration into the instance and reads every change
back to prove it landed. Its fields are validated strictly and an unknown
key is refused, so write it against
`okg/src/okg/substrate/deployments/chat_block.py` rather than from memory.
Leave the `ui:` sub-block out unless you need it — its keys are checked
against an artifact living in okg's repository, which a pip-only install
may not have.

## 7. Give it a model

Configure the provider in Open WebUI itself with your own API key. It is
deliberately not part of the deployment declaration, which carries no
credentials. Add the provider in the instance's admin settings, name the
model in `chat.models`, re-run `okg chat sync`.
