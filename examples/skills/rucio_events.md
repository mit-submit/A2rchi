# Rucio Events Monitoring - Query Guide

## Tool Usage Strategy
1. **Start with aggregation** for counting, grouping, or summary questions (e.g., "how many transfers failed?", "top error reasons").
2. **Use search** to find specific events by criteria. Results show compact summaries with key fields.
3. **Use fetch_rucio_document** to get full details of a specific event found in search results.
4. **Use pagination** (page=2, page=3) to browse through search results without flooding context.

## What This Index Contains
The `monit_prod_cms_rucio_raw_events*` index contains all CMS Rucio events.
Each document represents a single event: transfers, deletions, rule changes, dataset operations, etc.

## Event Types (data.event_type)

### Transfer Events
| Event Type | Meaning |
|------------|---------|
| `transfer-preparing` | Transfer being prepared |
| `transfer-queued` | Transfer queued for submission |
| `transfer-submitted` | Transfer submitted to FTS |
| `transfer-done` | Transfer completed successfully |
| `transfer-failed` | Transfer failed (check `data.reason`) |
| `transfer-submission_failed` | Failed to submit transfer to FTS |

### Deletion Events
| Event Type | Meaning |
|------------|---------|
| `deletion-done` | File deletion completed |
| `deletion-failed` | File deletion failed |
| `deletion-not-found` | File not found during deletion |

### Dataset/Container Events
| Event Type | Meaning |
|------------|---------|
| `create_cnt` | Container created |
| `register_cnt` | Container registered |
| `create_dts` | Dataset created |
| `close` | Dataset/container closed |
| `erase` | Dataset/container erased |
| `detach` | File detached from dataset |

### Rule Events
| Event Type | Meaning |
|------------|---------|
| `rule_ok` | Replication rule satisfied |
| `datasetlock_ok` | Dataset lock confirmed |
| `lost` | Replica marked as lost |

## Key Fields

### Transfer Events
| Field | Description | Example |
|-------|-------------|---------|
| `data.name` | Full LFN (file path) | `/store/mc/Run3.../file.root` |
| `data.event_type` | Event type | `transfer-failed` |
| `data.src_rse` | Source RSE | `T1_DE_KIT_Disk` |
| `data.dst_rse` | Destination RSE | `T2_CH_CERN` |
| `data.reason` | Error message (failures only) | `Connection timeout` |
| `data.transfer_id` | FTS job ID | `abc123-...` |
| `data.request_id` | Rucio request ID | `def456-...` |
| `data.activity` | Transfer activity | `Production Output`, `User Subscriptions` |
| `data.bytes` | File size in bytes | `2835136113` |
| `data.dataset` | Dataset name | `/DYto2L.../AODSIM#...` |
| `data.account` | Rucio account | `wmcore_output` |
| `data.transfer_link` | FTS monitoring link | `https://fts3-cms.cern.ch/...` |

### Deletion Events
**IMPORTANT:** For deletion events, use `data.rse` instead of `data.src_rse` or `data.dst_rse`.

| Field | Description | Example |
|-------|-------------|---------|
| `data.rse` | RSE where deletion occurred | `T2_US_Purdue` |
| `data.name` | Full LFN | `/store/mc/...` |
| `data.reason` | Error message (failures only) | `File not found` |

## RSE Naming Convention
- `T0_*` - Tier 0 (CERN)
- `T1_*_Disk` / `T1_*_Tape` - Tier 1 sites
- `T2_*` - Tier 2 sites
- `T3_*` - Tier 3 sites

## Common Query Patterns

### Transfer Queries

#### Find failed transfers for a specific file
```
data.name:"/store/mc/Run3Summer22.../file.root" AND data.event_type:transfer-failed
```

#### Find all transfer failures between two sites
```
data.src_rse:T1_DE_KIT* AND data.dst_rse:T2_CH_CERN AND data.event_type:transfer-failed
```

#### Find transfer failures with specific error
```
data.event_type:transfer-failed AND data.reason:*timeout*
```

#### Find transfers by activity
```
data.activity:"Production Output" AND data.event_type:transfer-done
```

### Deletion Queries

#### Find failed deletions at a specific RSE
```
data.event_type:deletion-failed AND data.rse:T2_US_Purdue
```

#### Find all deletion events for a file
```
data.name:"/store/mc/Run3.../file.root" AND data.event_type:deletion*
```

### Dataset/Rule Queries

#### Find all events for a dataset
```
data.dataset:"/DYto2L-4Jets*"
```

#### Find lost replicas
```
data.event_type:lost AND data.rse:T1_*
```

### General Queries

#### Count events by type (use max_results:1, look at total)
```
data.event_type:transfer-submitted AND data.dst_rse:T1_IT_CNAF_Tape
```

## Query Tips
1. **Use wildcards sparingly** - `data.src_rse:T1_*` is fine, but `data.name:*file*` is slow
2. **Be specific with event_type** - Query `transfer-failed` not `*fail*`
3. **Time ranges matter** - Default is 24h; use `from_time:"now-7d"` for older data
4. **Quote exact paths** - `data.name:"/store/mc/..."` not `data.name:/store/mc/...`
5. **Deletion events use `data.rse`** - NOT `data.src_rse` or `data.dst_rse`

## Aggregation Queries

Use the **aggregate_rucio_events** tool for counting, grouping, and statistics.

### When to Use Aggregation vs Search
- **Use aggregation** for: "top errors", "count by RSE", "how many transfers", "distribution of..."
- **Use search** for: "find the transfer", "show me events for file X", "what's the transfer_id for..."

### Aggregation Examples

#### Top transfer failure reasons
```
query: "data.event_type:transfer-failed"
group_by: "data.reason"
agg_type: "terms"
top_n: 10
```

#### Count transfers by destination RSE
```
query: "data.event_type:transfer-done"
group_by: "data.dst_rse"
agg_type: "terms"
```

#### Total bytes transferred to a site
```
query: "data.event_type:transfer-done AND data.dst_rse:T1_IT_CNAF*"
group_by: "data.bytes"
agg_type: "sum"
```

#### Number of failures per activity type
```
query: "data.event_type:transfer-failed"
group_by: "data.activity"
agg_type: "terms"
```

#### Count failed deletions by RSE
```
query: "data.event_type:deletion-failed"
group_by: "data.rse"
agg_type: "terms"
```

#### Count unique files with transfer failures
```
query: "data.event_type:transfer-failed"
group_by: "data.name"
agg_type: "cardinality"
```

### Aggregation Parameters
- `query`: Lucene filter (use `*` for all documents)
- `group_by`: Field to aggregate on (e.g., `data.reason`, `data.src_rse`)
- `agg_type`: 
  - `terms` - Count by unique values (most common)
  - `sum` - Total of numeric field
  - `avg` - Average value
  - `min` / `max` - Extreme values
  - `cardinality` - Count distinct values
- `top_n`: Number of buckets for terms aggregation (default: 10, max: 100)
- `from_time` / `to_time`: Time range (default: last 24h)

## Interpreting Results

### For failed transfers:
1. Check `data.reason` for error message
2. Use `data.transfer_link` to view FTS job details
3. Look at `data.src_rse` and `data.dst_rse` to identify problematic link

### For failed deletions:
1. Check `data.reason` for error message
2. Look at `data.rse` to identify the site with issues

### For aggregations:
1. Results show value and count in a table format
2. "(other values)" row shows count of values not in top N
3. Use specific filters to narrow down before aggregating
