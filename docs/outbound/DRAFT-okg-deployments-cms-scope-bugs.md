# DRAFT — not sent. For the okg-deployments/cms maintainers.

**Status: draft only.** Filing this on another team's repository is the
maintainer's call, not an agent's. Nothing here has been sent.

**Subject:** connectors can claim a complete scope after silently losing
input, which deletes good records

---

## Why we are writing

While porting your `cms/` connectors into the Archi v3 distribution we ran an
adversarial review over the ported code. It found one recurring defect class
that destroys data, and **most of the findings apply unchanged to the copies
still running in `okg-deployments/cms`** — we diffed against `main@f33a9c4`
and recorded, per finding, whether the bug is shared. We have fixed them on
our side; you have not been told, and the code is live for your team.

We have **not** run your deployment. Everything below is read from your code
plus reproductions we wrote against our port, which is line-comparable with
yours. Treat trigger frequency as our inference, not measurement.

## The failure mode

The substrate deletes records that are missing from a *completed* scope
(`missing_from_completed_scope`). A connector that loses part of its input —
a swallowed exception, a truncating cap, an empty API response, a schema
drift — and still reports `completed_scope=True` therefore tells the
substrate that everything it did not emit no longer exists. The next
reconcile retracts those records.

The dangerous property is that every one of these paths reports **healthy**.
There is no error, no partial status, no warning: an ingest that quietly lost
90% of its input looks exactly like a successful one.

## What we found in code shared with you

Blockers — silent data loss without an upstream outage:

| Where | What happens |
|---|---|
| `cms_sources/hypernews.py` | A per-forum listing failure is swallowed (`except Exception: continue`) while the run reports ok and complete. The failed forum's threads are retracted. |
| `cms_sources/hypernews.py` | A *total* fetch failure writes an empty `records.json` and claims ok/complete — retract-all — and every later run then replays that poisoned empty cache, so the source never self-heals. |
| `cms_sources/monit.py` | The rucio-dataset live page cache is keyed on a date-free fingerprint and never invalidated. After one successful run, later runs replay the cached pages with zero HTTP and still claim a complete scope: day 2 publishes day 1's datasets stamped with day 2's date. |

Majors — the same class, narrower triggers:

- `cms_sources/monit.py`: partial OpenSearch responses (`timed_out`, failed
  shards) accepted as complete; terms-aggregation caps truncate silently
  (`sum_other_doc_count` never read); a zero-bucket result claims a complete
  *empty* scope, so an index rename or a stalled pipeline retracts everything.
- `cms_sources/docs.py`: `max_pages` truncates the sitemap frontier before
  crawling, then claims a complete scope; non-HTML sitemap entries (PDFs) are
  regex-stripped into mojibake pages.
- `cms_sources/jira.py`: the parsed record count is never cross-checked
  against `meta.json`'s `record_count`, so a truncated-but-valid cache — a
  fetcher that died mid-pagination — ingests as complete.
- `cms_sources/siteconf.py`: a token that lost group visibility gets an
  HTTP-200 empty project list, reported ok and complete → retract-all.
- `cms_sources/cric.py`: an error-shaped responsibilities payload is read as
  "no responsibilities" under a complete scope.
- **Systemic**: item-level parse loops across cmssw / conddb / dbs / dqm /
  gocdb / wmstats / indico / hypernews silently skip records that are not
  dicts or lack an expected key. One upstream key rename turns a full cache
  into zero records with a healthy, complete-scope run.

Minor, same file lineage: truncating caps in `cmssw.py` (`limit`),
`hypernews.py` (`max_threads`), `siteconf.py` (`max_projects`); `conddb.py`'s
change probe missing `cmssw_records_path`; `gocdb.py` mis-parsing
scheme-prefixed endpoints; `indico.py` chunk-id collisions across events;
`cms_sources/alias.py` case-folding collapsing case-distinct dataset ids.

Not a scope bug but shared and worth knowing: the anonymizer's default email
pattern leaves the local part behind on real-world addresses
(`john.doe+ops@cern.ch` → `john.doe+`), so names survive redaction.

## The discipline we applied

Never claim `completed_scope` when any enumerated input failed, was
truncated, or parsed to nothing from non-empty input. Emitting partial data
with `completed_scope=False` is fine — claiming completeness is not. Note
`PREFLIGHT_STATUSES` has no `degraded`; we used the existing partial-crawl
idiom (`endpoint_failed` + records still emitted + no scope claim).

## Reproductions

Every finding has a regression test that **fails on the pre-fix code**, which
is the cheapest way for you to confirm a given bug exists in your copy: the
tests are written against the ported connectors, which are line-comparable
with yours. See `python/tests/sources/` and `python/tests/tools/` in
`archi-physics/archi@archi_v3`, and the per-finding writeups in
`pact/archive/circleback-fixes/notes-{catalogs,live,enrichment}.md`, which
record for each item whether it is shared with canonical.

## What we are asking

Nothing, beyond awareness. You may want the fixes, or your own; you may
decide some triggers cannot occur in your deployment. We would rather you
make that call knowing than not. If it is useful we can open PRs against
`okg-deployments/cms` with the fixes and their tests — say the word.
