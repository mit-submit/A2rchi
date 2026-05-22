# Rucio MCP — Usage Guide

## This interface is READ-ONLY

All `rucio_*` tools only read data. You cannot create, update, or delete anything
through them. When the user asks for a destructive action:

1. Use read tools to gather and confirm the current state
2. Tell the user you cannot perform the action directly
3. Provide the `rucio` CLI command they would run

### Example response for a non-read-only request

> I can't delete this from here — the Rucio tools I have are read-only. Here's
> what I found: [summary of current state]. To delete it yourself:
> ```
> rucio erase cms:/store/mc/.../DATASET
> ```

## Searching without a known identifier

Rucio does NOT expose a global "list datasets ordered by size/age/format" endpoint. If a question asks you to find data matching a property (e.g. "a dataset larger than X TB", "the newest container in scope Y", "MC samples from campaign Z") and you don't already have the DID, do not try to enumerate the whole catalog — sample purposefully:

1. Pick a representative scope (the most common one is `cms`; for a specific campaign, infer from `rucio_list_scopes()` if you don't know it).
2. Use `rucio_list_dids(scope, name_pattern="...", did_type="container")` with a coarse name pattern (e.g. wildcard `*`, or a campaign prefix like `Run3*`). Pass a `limit` (50-200) to bound the page.
3. For each returned DID that looks plausible, call `rucio_get_did(scope, name)` and read the `bytes` (and `length`) fields.
4. Filter and sort client-side; report the first match (or a small table of top matches) and stop.

Do NOT call `rucio_get_did` with a constructed-by-guess DID name — DID names follow strict patterns and a guessed name almost always returns "Data identifier not found". Always discover names via `rucio_list_dids` first.

## Reproducibility — include verification commands at the end

After any non-trivial response that used `rucio_*` tools, append a collapsible
`<details>` block with commands the user can run to reproduce the result. Skip
this only for trivial single-call lookups where the value is obviously the
tool's output.

Use this exact HTML format — the chat UI renders it as a collapsed section:

```
<details>
<summary>🔍 Commands to reproduce</summary>

\`\`\`bash
rucio list-dataset-replicas cms:/<actual/name/from/the/call>
\`\`\`

\`\`\`python
from rucio.client import Client
c = Client()
list(c.list_subscription_rules(account="transfer_ops", name="<actual_name>"))
\`\`\`

</details>
```

Rules:
- Use the **real arguments** from the actual calls, not placeholders.
- Mix shell and Python blocks when relevant — prefer CLI when available.
- If unsure whether a CLI exists, use the Python client.
- All Python snippets start with `from rucio.client import Client; c = Client()`.
- Do not invent flags that aren't in the mapping below.

### MCP tool → verification mapping

| MCP tool | Shell CLI | Python fallback |
|---|---|---|
| `list_dids(scope, name_pattern)` | `rucio list-dids <scope>:<pattern>` | `list(c.list_dids(scope, filters=[{"name": pattern}]))` |
| `get_did(scope, name)` | `rucio list-dids <scope>:<name>` | `c.get_did(scope, name)` |
| `list_content(scope, name)` | `rucio list-content <scope>:<name>` | `list(c.list_content(scope, name))` |
| `list_parent_dids(scope, name)` | `rucio list-parent-dids <scope>:<name>` | `list(c.list_parent_dids(scope, name))` |
| `get_metadata(scope, name)` | `rucio get-metadata <scope>:<name>` | `c.get_metadata(scope, name)` |
| `list_dataset_replicas(scope, name)` | `rucio list-dataset-replicas <scope>:<name>` | `list(c.list_dataset_replicas(scope, name))` |
| `list_replicas(dids)` | `rucio list-file-replicas <scope>:<name>` | `list(c.list_replicas([{"scope": s, "name": n}]))` |
| `list_did_rules(scope, name)` | `rucio list-rules <scope>:<name>` | `list(c.list_did_rules(scope, name))` |
| `list_replication_rules(filters)` | `rucio list-rules --account <account>` | `list(c.list_replication_rules(filters))` |
| `get_replication_rule(rule_id)` | `rucio rule-info <rule_id>` | `c.get_replication_rule(rule_id)` |
| `list_rses(rse_expression)` | `rucio list-rses --rses "<expr>"` | `list(c.list_rses(rse_expression=expr))` |
| `get_rse(rse)` | `rucio-admin rse info <rse>` | `c.get_rse(rse)` |
| `get_rse_usage(rse)` | `rucio-admin rse info <rse>` (see Usage) | `list(c.get_rse_usage(rse))` |
| `get_rse_limits(rse)` | — | `list(c.get_rse_limits(rse))` |
| `list_rse_attributes(rse)` | `rucio-admin rse info <rse>` | `c.list_rse_attributes(rse)` |
| `get_rse_protocols(rse)` | `rucio-admin rse info <rse>` | `c.get_protocols(rse)` |
| `get_distance(src, dst)` | — | `c.get_distance(src, dst)` |
| `list_transfer_limits()` | — | `list(c.list_transfer_limits())` |
| `list_accounts(...)` | `rucio list-accounts` | `list(c.list_accounts(...))` |
| `get_account(account)` | `rucio-admin account info <account>` | `c.get_account(account)` |
| `get_local_account_usage(account, rse)` | `rucio-admin account get-usage <account>` | `list(c.get_local_account_usage(account, rse))` |
| `get_local_account_limits(account)` | `rucio-admin account get-limits <account>` | `c.get_local_account_limits(account)` |
| `list_account_rules(account)` | `rucio list-rules --account <account>` | `list(c.list_account_rules(account))` |
| `list_subscriptions(name, account)` | `rucio list-subscriptions <account>` | `list(c.list_subscriptions(name, account))` |
| `list_subscription_rules(account, name)` | — | `list(c.list_subscription_rules(account, name))` |
| `get_dataset_locks(scope, name)` | `rucio list-dataset-locks <scope>:<name>` | `list(c.get_dataset_locks(scope, name))` |
| `get_dataset_locks_by_rse(rse)` | — | `list(c.get_dataset_locks_by_rse(rse))` |
| `list_scopes()` | `rucio list-scopes` | `c.list_scopes()` |
| `list_scopes_for_account(account)` | — | `c.list_scopes_for_account(account)` |
| `list_requests(...)` / `list_requests_history(...)` | — | `list(c.list_requests(...))` |

When no shell CLI exists (marked `—`), use Python only.