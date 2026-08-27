# cern-team quickstart

Get an Archi-on-OKG instance running on a fresh machine, holding a GitHub
repository and a GitLab repository, with a chat frontend in front of it.

`cern-team-demo.md` is the evidence runbook for the same bundle — longer,
and written to prove things rather than to be followed.

**What was actually run.** §1–4 and §6 were executed end to end on
2026-08-27 against an empty database — install, publish, read-back, and a
healthy chat instance — and the results are quoted inline. One thing is
**not** verified and says so where it appears: the TWiki source (§5), which
needs a CERN SSO cookie this host did not have.

---

## Before you start

### A Postgres okg can use

Not any Postgres. It needs six extensions, three of which
(`timescaledb`, `pg_cron`, `pg_textsearch`) must be loaded at server start,
so a stock install cannot be made to work after the fact. okg ships an image
that has them.

**One Postgres *instance* per deployment — not one database on a shared
server.** `pg_cron` is single-database, so each deployment gets its own
server process.

```bash
# Build the image once (from your okg clone).
cd /path/to/okg/ops/pg && podman build -t okg-pg17:local .

# Then one server per deployment. Loopback-only on purpose: the graph
# database should never be reachable off-host.
podman run -d --name myteam-pg --shm-size 2g \
  -e POSTGRES_PASSWORD=okg \
  -e POSTGRES_DB=myteam \
  -e POSTGRES_INITDB_ARGS="--locale=C --encoding=UTF8" \
  -p 127.0.0.1:5433:5432 \
  okg-pg17:local \
  postgres -c shared_preload_libraries=timescaledb,pg_cron,pg_textsearch \
           -c cron.database_name=myteam \
           -c max_connections=100
```

Verified 2026-08-27: all six extensions create cleanly on a server started
exactly like that.

That gives you the connection string used everywhere below — **this is a
real value, not a placeholder**:

```bash
export OKG_DSN='postgresql://postgres:okg@127.0.0.1:5433/myteam'
```

Change the password if the host is shared. `--shm-size 2g` matters: the
default 64 MB is too small for the index builds and produces intermittent
disk-full errors mid-run.

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
  --postgres-dsn "$OKG_DSN" \
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

> **Verified 2026-08-27** on one machine, rootless podman, no root access.

The bundle ships the `search:` and `chat:` blocks, so the deployment already
declares its chat site — you do not write one.

The chat app needs **its own database**, separate from the graph. okg checks
they really are different and refuses if not:

```bash
podman run -d --name okg-chat-pg \
  -e POSTGRES_PASSWORD=okg -e POSTGRES_DB=okg_chat_app \
  -p 127.0.0.1:5458:5432 docker.io/library/postgres:16

export OKG_CHAT_APP_DATABASE_URL='postgresql://postgres:okg@127.0.0.1:5458/okg_chat_app'
export OKG_CHAT_WEBUI_SECRET_KEY='choose-something'
export OKG_CHAT_MCP_TOKEN='choose-something'

okg-venv/bin/okg chat-instance up --deployment myteam \
  --container-runtime podman --instance-port 8099 --ready-timeout 300
okg-venv/bin/okg chat-instance status --deployment myteam
```

**Give it a plain `127.0.0.1` address, not a hostname.** okg validates the
database from the host and then rewrites the container's copy to reach back
through the container gateway, reporting the substitution rather than doing
it quietly. Handing it a container-only name like `host.docker.internal`
fails on the host; handing it the machine's own LAN address fails inside the
container. Loopback is the one that works, and it needs no root and no
second machine.

**Use a generous `--ready-timeout`.** On first boot Open WebUI downloads and
loads a sentence-embedding model, which can exceed the 120s default; okg
then tears the container down as never-started. 300 is comfortable, and
later starts are quick.

`--container-runtime` defaults to **docker**, so a podman host must say so —
and it is accepted by `up` only; `status` rejects it.

**Expect `status` to report the tools endpoint dead at this point**, and take
it seriously — it says so plainly: *"the site can be up and every tool call
still fail."* The site is running; the graph tools are a separate process,
which is the next step.

Then project the declaration into the running instance:

```bash
export OKG_CHAT_INSTANCE_URL=http://127.0.0.1:8099
export OKG_CHAT_ADMIN_TOKEN=...      # the instance admin key
okg-venv/bin/okg chat sync --deployment myteam
```

`sync` writes the declaration in and reads every change back to prove it
landed.

To remove it all again: `okg chat-instance down --deployment myteam
--container-runtime podman`, then `podman rm -f okg-chat-pg`.

## 7. Give it a model

The model provider is configured **inside Open WebUI**, not in the
deployment — the manifest has no field that could hold a credential, by
design.

A local ollama is the easiest option and needs no API key at all. Open
WebUI treats it as a first-class provider: point it at
`http://<host>:<port>` in the admin settings. Check where yours listens
before assuming the default — `OLLAMA_HOST` is often set to something other
than `11434`, and a container reaching *another* machine's ollama works
fine (only reaching back to its own host needs the gateway alias).

Then name the model in `chat_model` at install (or `chat.models` in the
manifest) and re-run `okg chat sync`.
