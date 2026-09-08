Re-acknowledgement of the external-distribution conformance wire schema, replacing
the one that named branch `f4081329`. This supersedes
https://github.com/mitdbg/okg/issues/1178#issuecomment-5440260363.

**The values**, for transcription into `trusted_external_review`:

```json
{
  "consumer_id": "archi-physics/archi:cern-team",
  "review_reference": "<this comment's URL>",
  "acknowledged_interface_revision": "1475c87d5d7fc60083bb0c87a9f9f3467a1a647a",
  "acknowledged_schema_digest": "sha256:db38e9bbbe571dbb9efec29192f786d851095ca412f5a5fda17d1af81f62e662",
  "source_revision": "e70b22043c563a95bb2850031f1a25a3472548a7",
  "acknowledgement_status": "current"
}
```

**Where each value comes from, so you can reproduce it rather than trust it.**

- `acknowledged_interface_revision` is `dev` itself, not a branch this time. The
  contract arrived on `dev` in `46c42c7e0b3dcea525174cabbcff0a6252f0cf52` via
  PR #1377 (merged 2026-08-28), with PR #1406 following on 2026-08-30. I pinned the
  revision to `dev` rather than to either commit because the digest below is only
  meaningful paired with the tree it was computed from.
- `acknowledged_schema_digest` is `conformance_schema_catalog()["schema_digest"]`
  evaluated at that revision. The value in the existing fixture,
  `sha256:301ec97f…`, no longer matches, which is the second reason the old
  acknowledgement reads as historical — separate from it naming a branch.
- `source_revision` is the head of `archi_v3`, our integration line.

**Two caveats, because this acknowledgement is narrower than it looks.**

First, it binds to a pair: one Archi revision and one OKG schema digest. The
validator requires `origin.source_revision` to equal the request subject's revision
and, for a `current` status, the acknowledged digest to equal the *current* catalog
digest. So any commit to either side invalidates it. Both sides are moving right now
— #1179 through #1185 are landing on yours, and on ours the cern-team bundle work
(the chat block, the shipped system prompt, the schema slices) is still on a branch
and not yet on `archi_v3`, so the revision above does **not** include it.

Second, and following from that: this cannot be the acknowledgement the real arm
finally runs against, because the real arm cannot run until the install verb exists,
by which time both revisions will have moved.

**So a question on the intended flow.** Is the acknowledgement meant to be re-issued
per conformance run, bound to the exact pair being validated? That is what the
checks imply, and if so the useful thing today is what this comment does — retire the
branch reference and establish the format — with a fresh one issued at the point the
real arm actually runs. If instead you intended a durable acknowledgement of the
*interface* that survives revision changes, then the binding to
`request.subject.source_revision` is stronger than that intent and worth loosening
before anyone depends on it.

Happy either way; I would rather ask now than have the real arm refuse on a
technicality we could have settled here.
