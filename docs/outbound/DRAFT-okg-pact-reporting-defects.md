# DRAFT — not sent. Two PACT reporting defects for the okg maintainers.

**Status: draft only.** Filing is the maintainer's call; nothing has been sent.

Both are reporting bugs, not logic bugs — the underlying state machine was
right every time. They are filed together because they share a failure mode:
**a view that disagrees with the state it is reporting on**, which is worse
than no view at all. In one working day these two produced four wrong
statements about programme state across two independent agent sessions, each
of which had to be corrected by a third check against the artifact.

## 1. `--format=review-queue` lists retired changes as awaiting a decision

`okg pact view --format=review-queue` reported "3 change(s) awaiting a
decision", and all three were `state: retired`. A retired change cannot await
a decision.

Observed on our interim ledger deployment with three changes retired by an
operator's `okg pact decide --verdict approve`. Two sessions reading the same
command minutes apart drew opposite conclusions about whether the queue was
empty — one reported "all approved, queue empty", the other "three awaiting
approval". Both were reading the tool correctly.

Reliable alternative, for anyone hitting this: `okg pact view <id>
--format=status` per change reports `state` truthfully, and
`--format=list --status all` agrees with it.

## 2. `evidence_gaps: []` while `derived_state.reason` says gaps remain

The same status payload can contain:

```yaml
evidence_gaps: []
missing_evidence: []
derived_state:
  state: in_progress
  reason: "tasks terminal but evidence gaps remain"
```

Both gap lists empty, and the reason says gaps remain. There is no way to
find the cause from any view — we had to call `manifest_condition()` and
`staleness_report()` directly against the manifest to learn that
`evidence_satisfied` was false because the **scope-drift guard** had revoked
every requirement's evidence: a merge had touched the declared scope after the
evidence was recorded, so it stopped counting (`review.py:146-165`).

The guard is correct and, we think, well designed — evidence that predates a
change to the scope it covers *should* stop counting. The defect is purely
that the reason string promises a gap list which the gap fields do not
contain, so the operator is told there is a problem and given nothing to act
on. Surfacing the revocation — the requirement ids, the `code_changed_at`, the
evidence ids revoked — would have turned a two-session diagnosis into one
command.

## A consequence worth stating, if it is not already tracked

Because the guard is scope-based, **any change touching a path revokes the
evidence of every other open change whose declared scope covers that path**.
The widest-scoped change in a programme therefore accrues a re-evidencing bill
that grows with every unrelated merge, and can be blocked indefinitely by
other people's work. That is defensible behaviour, but it makes leaving
changes open expensive in a way nothing currently warns about. A hint in the
status output — "N requirements' evidence revoked by commits since
<timestamp>" — would make the cost visible while it is still cheap to pay.

## Environment

okg `dev` @ `f5ec3b58d`, PACT v4, external consumer repo
(`archi-physics/archi@archi_v3`) with the interim `archi-pact` ledger
deployment. Both reproduced repeatedly across a working day.
