Answering the enricher question, with the surface list and migration notes you asked
for. Short version: **ship connectors first**, with one condition.

## The answer

Scope #1181's first pass to connectors. Do not hold it for the enricher read surface.

Our exposure is lopsided — roughly 25 connectors against 6 enrichers — so a
connectors-only pass covers most of what we depend on. More importantly, the enricher
problem is genuinely hard, and it currently sits in front of the rest of Sprint 11,
including #1185, which is the work that proves the whole distribution functions. That
is the wrong thing to block.

**The condition: keep the existing enricher path working until its replacement
exists.** Our five database-backed enrichers work today. If the boundary lint or the
cleanup removes the live-connection path before there is something to move to, they
break with nowhere to go. Deferring the design is fine; deferring the design while
withdrawing the current mechanism is not.

## Our numbers match yours

Five of our six enrichers cannot be expressed without a live connection — the same
ratio as your 34 of 38. Only the anonymizer stays out of the database.

| Enricher | What it derives |
|---|---|
| `jira_affects` | links a JIRA issue to the software it affects |
| `meeting_document_reference_rollup` | links a meeting to what its documents reference |
| `chunk_reference_rollup` | rolls chunk-level references up to the parent document |
| `alias` | resolves that two names denote the same thing |
| `dqm_run_range` | attaches certifications to the runs they cover |
| `anonymizer` | strips personal data — the only one needing no reads |

## The read surface we would need

In rough order of how hard they look to express:

1. **Multi-hop traversal.** `meeting_document_reference_rollup` walks meeting →
   `contains` → document → `contains` → chunk → `references` → target: three hops,
   six joins, in one query. A one-node-at-a-time read API turns this into an N+1
   walk over the whole meeting corpus.
2. **Incremental change detection over the fact log.** Two of ours read
   `okg.node_facts` / `okg.edge_facts` with `fact_id > <watermark>` — "what changed
   since I last ran". This is not a graph read at all; it is a monotonic cursor over
   the append-only log, and it is the capability most likely to be missing from any
   node-and-edge-shaped API. Without it these become full rescans.
3. **Aggregation over a traversal.** Three of ours `GROUP BY` across the join to roll
   many child references into one parent edge, with counts.
4. **Anti-join on existence.** `dqm_run_range` does
   `LEFT JOIN okg.graph_edges existing … WHERE existing.edge_id IS NULL` — create this
   edge only where one does not already exist. Expressible as a read-then-filter, but
   only if the read is bulk.
5. **Scan by subtype, returning attributes.** Not just ids: `alias` and
   `dqm_run_range` both need `attrs`.
6. **Range join.** `dqm_run_range` joins certifications to runs by run-number
   interval, not by equality.
7. **Edge existence by id set.** A bulk `edge_id = ANY(...)` membership check.

**What we do not need, so you can leave it out of a first cut:** vector top-k. None
of our enrichers use similarity search.

**On the write side** we are simpler than the read side suggests: we emit derived
edges through `insert_deterministic_edges` with `DerivedEdgeCandidate`, and write no
node facts. If the first enricher pass covered reads plus deterministic derived
edges, it would cover all six of ours.

## Migration notes

The names our enrichers import today, as the surface list you asked for:

- `okg.substrate.enrichers.base` — `EnrichResult`, `IncrementalContext`
- `okg.substrate.enrichers.derived_edges` — `DerivedEdgeCandidate`,
  `insert_deterministic_edges`
- `okg.substrate.alias.protocol` — `AliasMatch`
- `okg.substrate.library.linkers.declarative` — `DeclarativeLinker`
- `okg.substrate.library.linkers` — **`_chronos`**

That last one is worth flagging: we depend on an underscore-prefixed name, private by
Python convention, never mind by your boundary rules. It is the clearest example of
why this SDK is worth doing, and if the connectors-only pass can absorb nothing else
from this list, that one is the one to take.

Two smaller points on your note. Your §2 is right that enrichers are handed a live
connection inside a savepoint on the publish cycle's transaction, and that moving
them out relocates derived edges relative to the source facts' generation boundary —
that is a semantic change for us, not only a plumbing one, so we would want it called
out explicitly rather than arriving as a side effect. And we agree with ordering the
read surface *before* the enricher protocol; designing the protocol first would just
encode whatever the reads happen to allow.
