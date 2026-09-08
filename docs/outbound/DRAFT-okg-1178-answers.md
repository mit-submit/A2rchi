Answers to the seven open questions, in order. I checked the claims I am agreeing
with against `dev` at `1475c87d5d` rather than taking them on trust, and two of them
have a consequence for our side that is better settled now than discovered during the
real arm. Thank you for the design notes — the corrections comment in particular
saved us from planning around two things that had already landed.

**1. Module providers get their own PACT.** Agreed, and already done:
#1737 carries `pact/changes/module-providers-in-composition/`. Nothing to decide;
flagging it only so the question is not held open.

**2. Package v2, with `okg_compatibility: {min_version, max_version_exclusive}`.**
Agreed. Two reasons, one of them stronger than the one in the note:

- `contract_versions` is checked by literal membership against
  `CONTRACT_VERSION = "v1"` (`lock.py:711-726`, `models.py:29`), so a range placed
  there is refused as `compatibility_contract_unsupported` rather than interpreted.
- More decisive: the two fields answer different questions. `contract_versions` says
  which deployment-product *contract revision* an artifact speaks. An OKG version
  window says which OKG *releases* can run a distribution. Overloading the first
  would change the meaning of a check that other artifacts already depend on, to
  express something it was never asked.

Archi will declare a window rather than a point.

**3. Yes, and the spelling is `conformance_declared_optional_credential_unavailable`.**
The scoping is exactly what we asked for: the validator requires `optional_credential`
to be present and `reference` to equal `optional_credential.binding_id`
(`conformance.py:881-897`), so an unsupplied credential is admissible while missing
fetch tooling still fails. That distinction was the whole point — of our nineteen
clean failures only five were genuinely missing credentials, and eight are tooling
nobody has written.

Two things follow that we would rather settle now:

- The code is admissible only for a **manifest-declared** credential binding. Our
  bundle deliberately declares none on default-selected sources — that is
  test-enforced on our side — and the credentialed sources ship as `.example` files a
  site opts into. So as things stand, enabling one of those on the real arm without
  its credential is a hard `fail`, not `blocked`. We would rather declare optional
  credential bindings for sources we ship but do not select. Tell us if that is the
  wrong shape before we build it.
- The closed profile pins `arm == "first_publish"` and `phase == "apply"`. Our
  credential-gated connectors fail while fetching during ingest. Please confirm that
  lands in that arm and phase, otherwise the code cannot be used where we actually
  need it.

**4. Both as receipt fields the install verb emits.** Accepted; both types are on
`dev` and are unambiguous.

`ConformanceLegacyAuthorityEvidence` needs one agreement before we can fill it. It
requires a declared inventory whose entry count equals `declared_inputs`, with
`declared_inputs == migrated_inputs + refused_inputs`, `unresolved_inputs: 0` and
`fallback_authority_absent: True`. That is ours to produce on the real arm, and it
turns on a definition: **what counts as a "legacy input"?** On our side the
candidates are the `$OKG_PROFILES_DIR` authoring-checkout path, the
`okg install --profile` invocation, and the hand-pruned schema copies the current
install still requires. If "input" is narrower or broader than that, we will build
the inventory against the wrong denominator and the count will bind to nothing
meaningful. Name it and we will produce it.

**5. Hermetic fixture per pull request, real container nightly.** Agreed. Two things
worth recording explicitly:

- A failed real arm must not be waivable by a green hermetic one. The note already
  says this; please keep it.
- The real arm can never gate your merges. It needs our host, our private data and a
  human, so its schedule must not become a dependency of yours. Treating it as a
  nightly signal you can read, rather than a gate you wait on, is the only version
  that works.

On cost: agreed that 400s is a placeholder and should be measured before it is
enforced, and that the kill is the only real runaway guard while a clean overrun
still exits 0.

**6. Yes — owed, and mine.** I will re-acknowledge against the merged tree rather
than the branch, with the five fields matched exactly, so the real arm stops
returning `conformance_external_acknowledgement_pending`. Posting separately.

**7. The chain order holds.** #1180 does not need to move earlier for us: our
deployment is single-operator today, so per-user authentication is not what is
blocking anything on our side. If a demo deployment needs it sooner, move it on that
basis rather than ours.

One exception, which is not really about ordering. #1183 carries a defect that is
producing confidently wrong answers today, in a path people are already using: a
synced instance opens on a model that has no graph tools, because the field naming the
preset's base model is also the field that sets the site default. I will file the
detail with line references on #1183 itself. The fix looks small, and it should not
have to wait for #1183's slot in the chain.
