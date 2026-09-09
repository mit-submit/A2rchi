Filing this on #1179 because it owns the install verb, and because we have something
you cannot easily get from inside the repo: measurements from the only external
distribution actually being installed by people who did not write OKG.

**The ask, plainly: we would like installing an OKG deployment to be easy, and today
it is not.** Not broken — we have a working deployment — but clunky in ways that cost
a new operator an afternoon, and we would rather say so now, while #1179 is being
designed, than after.

## What it costs today

Standing up a graph from nothing is **eleven steps**, one of which is *expected to
fail*: create a venv, clone OKG, pip-install it editable, pip-install the
distribution, build a Postgres image, run it with three `shared_preload_libraries`,
create and `git init` a deployments directory, export four environment variables, run
`okg install` (which exits non-zero), commit what it generated, then run
`okg run --once --apply` to publish what the install staged.

Adding the chat frontend takes it to roughly eighteen, including two `curl` calls
against OpenWebUI's admin API that no OKG command can make for you (filed separately
on #1183).

Much of that is inherent to a database-backed service and we are not complaining
about it. Three of them are not inherent, and we found all three today by re-running
our own documented procedure against current `dev` — a procedure that worked in
August and now publishes nothing.

## The three that are not inherent

1. **`okg install` cannot satisfy its own provenance requirement on a fresh
   deployment.** It generates `deployment.yaml`, the schemas, the skills and
   `source_registry.yaml`, then refuses to publish because those exact files are
   untracked — inside one command
   (`publisher/repository_provenance.py:105` checks
   `git status --porcelain --untracked-files=all`). There is no moment at which an
   operator could have committed them. The working sequence is install, let it fail,
   commit, then `okg run --once --apply`. We now document "this command will fail,
   and that is expected", which we are not happy about.
   *(`--no-publish` is not the split it appears to be — it skips database
   provisioning entirely, leaving zero tables in the `okg` schema, and the follow-up
   ingest dies on a missing admission ledger.)*
2. **A non-editable install breaks publishing.** OKG locates its own revision by
   looking for a Git checkout around its installed module, so `pip install ./okg`
   puts it in `site-packages` and fails five authority checks. `pip install -e` is
   now mandatory for reasons unrelated to editing OKG, which is surprising enough
   that our document had told people the opposite.
3. **A failed run leaves the deployment wedged.** `staged source work was not
   published; refusing to start new source writes`. Fixing the original problem and
   re-running does not clear it. Neither does `okg up`, which the error hint
   recommends — it fails differently, at its backup phase. The only recovery we found
   is a full teardown, which for a first-time operator reads as "start over and hope".

## What we would ask of the verb you are building

Not a redesign — #1185's own definition of done is already the thing we want: *from a
clean environment with no `$OKG_PROFILES_DIR`, no checkout and no path injection, an
operator installs one pinned package, selects a bundle, and gets a published
generation.* Three requirements so the new verb does not inherit the old traps:

- **It must be able to succeed on a fresh deployment.** If provenance requires
  committed configuration, the verb that writes that configuration should commit it,
  or exempt what it just wrote. A command that cannot succeed the first time is not
  an install.
- **It must not require an editable install.** Pin the framework revision explicitly
  if that is what provenance needs, rather than inferring it from where the code
  happens to sit on disk.
- **A failure must leave a retryable state.** Wedged-until-teardown is the single
  most expensive behaviour here, because it turns any other error into a restart.

## One thing that is missing rather than awkward

OKG stands up the chat container for the operator — `okg chat-instance up` creates
it, wires its database and reconciles it. It does not do the same for the graph
database, which the operator builds by hand from a Dockerfile in the repo and passes
in as a DSN.

That asymmetry is the interesting part. The harder, more opinionated container is
already managed; the one with a documented image in your own tree is not. If
`chat-instance up` is the right shape, something like it for the graph database would
remove two of the eleven steps and the entire class of "I built the Postgres wrong"
failures — of which we have hit several, including a 64 MB `/dev/shm` default that
fails partway through index builds.

We are glad to test whatever lands, on a real deployment with real data, and to
report back in this shape.
