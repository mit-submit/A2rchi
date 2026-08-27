# cern-team quickstart

From nothing to a knowledge graph you can query, holding a GitHub
repository and a GitLab repository, with a chat frontend in front of it.

Follow it top to bottom. Every command is real — no placeholders to fill
in. Where a value is yours to choose, it says so.

**What is proven.** Steps 1–6 were executed end to end against an empty
database on 2026-08-27, and their results are quoted inline. Step 8 (TWiki)
is the one part not verified, and says so where it appears.

**You need:** a machine with `podman` (or `docker`), Python 3.12+, `git`,
and read access to the private `mitdbg/okg` repository. No root required.

---

## 1. Get the code

```bash
mkdir -p ~/archi-quickstart && cd ~/archi-quickstart

python3.12 -m venv okg-venv          # 3.12+; archi refuses anything older

git clone git@github.com:mitdbg/okg.git
okg-venv/bin/pip install ./okg       # add -e only if you plan to edit okg

okg-venv/bin/pip install \
  "archi @ git+https://github.com/archi-physics/archi@archi_v3#subdirectory=python"
```

**Why a clone for okg but not for archi.** okg is a private repository and
is not on PyPI, so pip cannot fetch it for you. archi is public, so pip
takes it straight from git — no clone, no wheel build.

**Do not `pip install archi`.** That name on PyPI belongs to an unrelated
archive library. Ours is unpublished; the git URL above is the only correct
source.

*If pip is slow and noisy with connection retries, that is site networking
rather than anything here — it recovers on retry. Check whether the install
actually failed before assuming it did.*

## 2. Start a Postgres

okg needs six extensions, three of which must be loaded at server start, so
a stock Postgres cannot be adapted afterwards. okg ships an image with them.

**One Postgres server per deployment**, not one database on a shared
server — `pg_cron` is single-database.

```bash
cd okg/ops/pg && podman build -t okg-pg17:local . && cd -

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

The build pulls a large TimescaleDB base image, so it takes a while the
first time. `--shm-size 2g` is not optional: the 64 MB default is too small
for the index builds and fails partway through.

## 3. Install the bundle

```bash
export OKG_PROFILES_DIR="$(okg-venv/bin/archi-profiles-dir)"
export OKG_DEPLOYMENTS_DIR="$PWD/deployments"
export ARCHI_DATA_ROOT="$PWD/deployments/myteam"
export OKG_DSN='postgresql://postgres:okg@127.0.0.1:5433/myteam'

okg-venv/bin/okg install --profile cern-team \
  --deployment-name myteam \
  --postgres-dsn "$OKG_DSN" \
  --github-repo-name click --github-repo-url https://github.com/pallets/click \
  --gitlab-repo-name ci-example \
  --gitlab-repo-url https://gitlab.cern.ch/gitlabci-examples/build_docker_image.git
```

**That one command is the whole install.** It creates the extensions,
migrates, materialises the distribution's schemas, loads the catalog,
clones both repositories, ingests, and publishes.

Swap in your own repositories. `--*-repo-name` is just a short label for
the graph; `--*-repo-url` is what gets cloned. Add `--chat-site-name` and
`--chat-model` if you want to name the chat site now rather than later.

**Give the repository URLs here.** They look like noise, but the bundle
selects both repository connectors by default, and a *selected* source that
cannot run blocks the publish for every other source too — so an install
without them ends with nothing published. Step 4 is how to fill them in
afterwards if you would rather not decide now, and how to change them
later.

**You never run `git clone`** — the connectors own their checkouts,
cloning on first run and fast-forwarding afterwards.

**Expected result:** `first publish complete`, roughly 2,586 nodes / 2,279
edges for the two repositories above.

*If the catalog load fails on authentication, the migration created the
loopback roles without passwords. Set them, then re-run the ingest (not the
install — see step 4):*

```bash
podman exec myteam-pg psql -U postgres -d myteam \
  -c "ALTER ROLE okg_mcp PASSWORD 'okg_mcp'"
podman exec myteam-pg psql -U postgres -d myteam \
  -c "ALTER ROLE app_rw PASSWORD 'okg_rw'"
podman exec myteam-pg psql -U postgres -d myteam \
  -c "ALTER ROLE app_ro PASSWORD 'okg_ro'"
```

## 4. Changing or adding repositories later

**`okg install` will not re-run over an existing deployment** — it refuses
rather than overwrite, and `--force --yes` rebuilds the directory. So this
is the route for any later change, and the recovery if you installed
without URLs.

Edit `deployments/myteam/source_registry.yaml`, and under `github_repo` and
`gitlab_repo` set the two values:

```yaml
    params:
      repo: click
      url: https://github.com/pallets/click
```

Then apply and publish:

```bash
cd deployments/myteam
okg-venv/bin/okg catalog ownership claim --deployment . --json
okg-venv/bin/okg catalog load --deployment . --apply --json
okg-venv/bin/okg ingest --deployment myteam --progress
cd -
```

**Not interested in repositories at all?** Leave them out of the install and
exclude them from the ingest instead — the CMSSW catalog alone publishes
fine:

```bash
okg-venv/bin/okg ingest --deployment myteam --progress \
  --exclude github_repo,gitlab_repo
```

## 5. Confirm it published

```bash
okg-venv/bin/okg status --deployment myteam --json
okg-venv/bin/okg search --deployment myteam --query "docker image build"
```

`latest_published_status` must be `published` and
`latest_published_generation` must not be null. If the generation is null,
nothing was published and search will refuse — see step 7.

The search returns real files from the GitLab repository, pinned to that
generation. Direct SQL against the graph tables is refused by design.

## 6. Start the chat frontend

The bundle already declares the chat site, so there is nothing to write.
The chat app needs **its own** database, separate from the graph; okg
checks they really are distinct and refuses if not.

```bash
podman run -d --name myteam-chat-pg \
  -e POSTGRES_PASSWORD=CHOOSE-A-REAL-PASSWORD -e POSTGRES_DB=okg_chat_app \
  -p 0.0.0.0:5458:5432 docker.io/library/postgres:16

export OKG_CHAT_APP_DATABASE_URL='postgresql://postgres:CHOOSE-A-REAL-PASSWORD@127.0.0.1:5458/okg_chat_app'
export OKG_CHAT_WEBUI_SECRET_KEY='choose-anything'
export OKG_CHAT_MCP_TOKEN='choose-anything'

okg-venv/bin/okg chat-instance up --deployment myteam \
  --container-runtime podman --instance-port 8099 --ready-timeout 300
okg-venv/bin/okg chat-instance status --deployment myteam
```

**Note `0.0.0.0` on the chat database, and only there.** Unlike the graph
database, this one is read from *inside* a container, and under rootless
podman a loopback-only published port is not reachable from one — the
container connects and the connection dies with *"server closed the
connection unexpectedly"*. Binding beyond loopback does mean other machines
can reach it, so give it a real password rather than a placeholder. The
graph database stays loopback-only and is unaffected.

**Give the DSN `127.0.0.1`, not a hostname.** okg validates the database from
the host and rewrites the container's copy to reach back through the
container gateway, reporting the substitution rather than doing it
silently. A container-only name fails host-side; the machine's own network
address fails container-side. Loopback is the one that works, and it needs
no root and no second machine.

**`--ready-timeout 300` matters.** On first start Open WebUI downloads and
loads a sentence-embedding model, which overruns the 120-second default;
okg then tears the container down as never-started.

`--container-runtime podman` is needed because the default is docker — and
it is accepted by `up` only, not by `status`.

Expect `status` to report the tools endpoint dead. It is telling the truth:
the site is up, but the graph tools are a separate process. **Take it
seriously — the site can be up and every tool call still fail.**

Open `http://127.0.0.1:8099` and point it at your model provider in the
admin settings. A local ollama needs no API key at all — check which port
yours listens on rather than assuming the default, and note that a
container reaching *another* machine's ollama works fine.

## 7. If the publish is blocked

Every *selected* source must finish or none of them publish — including the
ones that worked. So a single source you cannot feed blocks everything.

The bundle selects only three by default and none needs a credential. If
you enabled others, exclude what you cannot feed and re-run:

```bash
okg-venv/bin/okg ingest --deployment myteam --progress \
  --exclude docsite,jira,twiki_eos,twiki_crawl
```

## 8. Adding a TWiki

> **Not verified** — no CERN SSO cookie was available where this was
> written. The requirement below is evidenced; the working configuration is
> not.

**A CERN SSO cookie is required even for a public wiki page.** The
connector reads a topic's source through `?raw=all`, and CERN redirects
that to its login page even where the rendered page opens for anyone. The
connector detects the login bounce and refuses to ingest it — correctly: a
login page stored as wiki text is worse than a clean failure.

Run the CERN SSO login helper (it prompts for a code from your
authenticator), then enable the connector and reinstall into a fresh
deployment:

```bash
export CERN_TWIKI_COOKIE_FILE=/path/to/cookies.txt
mv "$OKG_PROFILES_DIR/cern-team/source-defaults/twiki_crawl.yaml.example" \
   "$OKG_PROFILES_DIR/cern-team/source-defaults/twiki_crawl.yaml"
```

Leave the crawl depth at `0`. At depth 1 it follows every link on the seed
page, and one unreachable topic makes the whole run refuse to claim it saw
everything.

## Adding other sources

The bundle ships JIRA, documentation sites, Indico and both TWiki readers
as `.yaml.example`. Rename one to `.yaml`, supply its credential by
environment variable, and reinstall.

For something the bundle does not ship, `okg add <source> --deployment
myteam` writes a registry entry — deliberately an incomplete one, reporting
`activation_blocked` rather than activating a source it cannot fully
describe. You finish it in `source_registry.yaml`, then re-claim and
re-load.

## Starting over

The ownership claim lives in the **database**, not the deployment folder, so
deleting the folder alone is not a reset — the next publish will refuse with
`catalog_ownership_mismatch` because the registry no longer matches what was
claimed. Remove the container to clear it.

```bash
okg-venv/bin/okg chat-instance down --deployment myteam --container-runtime podman
podman rm -f myteam-pg myteam-chat-pg
rm -rf deployments
```

Keep `okg-venv` and the okg clone — nothing is wrong with them, and
reinstalling costs you the slow part again.

**If you hit `catalog_ownership_mismatch` and would rather not start over,**
re-running the claim is enough — it re-records the current registry as the
owner:

```bash
cd deployments/myteam
okg-venv/bin/okg catalog ownership claim --deployment . --json
okg-venv/bin/okg catalog load --deployment . --apply --json
okg-venv/bin/okg ingest --deployment myteam --progress
cd -
```

**`okg install` is not a retry.** It refuses to run over an existing
deployment directory, and where it does run it cannot fix a stale claim —
it will re-ingest happily and still fail to publish.

## What still needs a person

- **`OKG_PROFILES_DIR`** — okg cannot find bundles inside an installed
  package, so the variable is required even though the wheel carries them.
- **The role passwords**, when the server authenticates by password.
- **The TWiki cookie** — the CERN SSO login is interactive by design.
