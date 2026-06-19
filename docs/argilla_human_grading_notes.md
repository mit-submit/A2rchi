# Argilla Human-Grading Notes

Last updated: 2026-06-19.

The paper used Argilla for blinded human grading of sampled benchmark answers.
This note is a sanitized handoff for collaborators. It intentionally omits
OAuth secrets, API keys, grader account lists, passwords, raw databases, and
unblinding maps.

## Architecture

The paper grading service used a small submit76 stack:

```text
Internet or VPN
  -> Caddy TLS proxy
  -> OAuth proxy
  -> Argilla server
  -> Argilla storage volumes
```

The OAuth layer controlled access to the host. Argilla's own account model was
used inside the protected service for annotator access.

## Data Flow

1. Select a paper grading subset from approved benchmark result JSONs.
2. Blind model/config labels before pushing records to Argilla.
3. Ask graders to score answers with the agreed rubric.
4. Export completed records.
5. Apply the unblinding map locally for analysis.
6. Aggregate human scores into the paper tables.

Raw production traces and unblinding maps should stay outside the shared branch.
Only approved question text and derived, blinded grading records should be
shared with graders.

## Relevant Code Paths

| Path | Role |
|---|---|
| `src/utils/benchmark_argilla.py` | Generic Archi Argilla integration. |
| `docs/docs/benchmarking.md` | User-facing docs for `archi evaluate` and Argilla support. |
| `.scratch/push_grading_5way_v9.py` | Historical paper push script for the blinded main grading sample. |
| `.scratch/grading_progress_both.sh` | Historical progress checker against the live grading DB. |
| `.scratch/backup_grading_data.sh` | Historical backup helper for the live service. |

The `.scratch/` files above are provenance records. Before reusing them for a
new grading round, promote a cleaned copy into a tracked script location and
remove absolute paths, account names, secrets, and run-specific dataset ids.

## What Not To Commit

- Argilla API keys or auth secrets.
- OAuth client ids/secrets.
- Grader email lists or account-password files.
- Argilla SQLite/Postgres/Elasticsearch data directories.
- Unblinding maps before the analysis is complete.
- Raw exported grading records if they contain private grader metadata.

## Minimal Reproduction Guidance

For a new grading round, use a fresh Argilla workspace and a new blinded dataset
name. Keep a local manifest that maps blinded labels to benchmark result files,
but store it outside the shared repository until grading is complete.
