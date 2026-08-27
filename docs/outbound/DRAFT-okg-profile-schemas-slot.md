# DRAFT — not sent. Proposal for the okg maintainers.

**Status: draft only.** Sending this is the maintainer's call; nothing
has been sent.

**Subject:** let an install profile ship its own schema files, the way it
already ships its own playbooks

**Relates to:** okg#1179 (external schemas and bundles), okg#1185
(end-to-end external-distribution proof), okg#1178 (Archi ↔ OKG channel)

---

## The ask, in one sentence

Add a `schemas:` key to the install-profile contract that materializes
files into the new deployment's `schemas/` directory, exactly parallel to
the `skills:` key that already materializes playbooks.

## Why this specific shape

You already built this mechanism. `profile.yaml` declares `skills:
skills/`, and `profile_init.py` copies every file verbatim into
`<deployment>/skills/`. Our install materializes 21 playbooks that way
and it works perfectly.

Schema files are the same kind of asset — distribution-owned, static,
copied verbatim — and they have no equivalent channel. We are asking for
the slot, not a new mechanism.

## Why it doesn't work today

`profile_init.py` has a comment that states the assumption exactly:

> Profiles compose ontology modules from the substrate library, so a
> fresh profile scaffold needs no deployment-native subtypes

That is true for every profile OKG has shipped so far, because their
vocabulary all lives in OKG's own module library. Archi is the first
consumer where it does not hold. We are a *distribution*: we bring our
own HEP vocabulary — `cmssw_release`, `jira_issue`, `documentation_page`,
`site`, and the rest. It ships inside our wheel, and the profile contract
has nowhere to declare it.

## What it costs, concretely

This is the part we would ask you to weigh, because the cost is much
larger than the gap looks.

`okg install` already does the whole job: provisions Postgres, scaffolds
the deployment, sets up sources, migrates, loads the catalog and
publishes. On a fresh machine that is **one command**.

We cannot use it. Our schema files must land *between* the scaffold and
the catalog load, so every archi install passes `--no-publish` and then
re-does by hand what the installer would have done: create the
extensions, migrate, claim, load, publish. Our own `profile.yaml` records
why:

> `--no-publish` matters: two documented post-install steps (schema slice
> copy from the installed archi wheel) must land before the first catalog
> load, so the bundled first-publish path would fail on a fresh instance.

So a missing copy step turns a one-command install into an eight-step
runbook — and every one of those steps is a chance for an operator to get
it wrong. This is the single largest source of friction in the
external-distribution path, and it is the reason our quickstart document
is as long as it is.

There is a second, smaller step in the same class: `okg migrate` creates
the loopback roles without passwords on a password-authenticated server,
so `okg catalog load` fails until the operator sets three passwords by
hand. Same shape — a gap in the automated path that the operator has to
fill manually. Worth fixing alongside, though it is independent.

## What we considered instead, and why it doesn't work

**Ship the vocabulary as an ontology module.** This is the architecturally
cleaner answer and we would prefer it. It is blocked: `load_modules_root()`
is package-locked, so OKG cannot compose modules that live in our wheel.
That is the larger half of okg#1179 and we assume it lands on its own
schedule. The `schemas:` slot is the small thing that unblocks external
distributions *now*, independent of the module work.

**A helper command on our side.** We can reduce four `cp` commands to one
`archi` command, and we intend to. It does not help: the step still has to
happen at the right moment, which still means `--no-publish`, which still
means the operator re-does the rest of the install by hand.

## Suggested contract

```yaml
# in a profile's profile.yaml, alongside the existing keys
modules: modules.yaml
source_defaults: source-defaults/
invariants: invariants.yaml
skills: skills/
schemas: schemas/          # <-- proposed: copied into <deployment>/schemas/
```

Semantics we would expect, all matching `skills:`:

- copied verbatim, preserving subdirectories (we need `bridges/`)
- written before the first catalog load, so the bundled publish path sees
  them
- an absent key changes nothing for existing profiles
- no templating and no merge — these are static assets, and a distribution
  that wants templating can generate the files before packaging

## What we can offer

We are a live external consumer with a working end-to-end install, so we
can validate this quickly. If a `schemas:` slot lands, we will re-run the
full cern-team install without `--no-publish` and report whether the
one-command path holds for an external distribution — which is most of
what okg#1185 is asking for anyway.

Happy to send a patch if that is easier than a request.
