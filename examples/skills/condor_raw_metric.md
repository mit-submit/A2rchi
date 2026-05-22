# HTCondor Raw Metric — OpenSearch Index Guide

## Tool Usage Strategy
1. **Start with aggregation** for counting, grouping, or summary questions (e.g., "how many jobs failed?", "average CPU efficiency").
2. **Use search** to find specific jobs by criteria. Results show compact summaries with key fields.
3. **Use fetch_condor_document** to get full details of a specific job found in search results.
4. **Use pagination** (page=2, page=3) to browse through search results without flooding context.

## Index

`monit_prod_condor_raw_metric*` (date-partitioned, e.g. `monit_prod_condor_raw_metric-2026-03-04`)

## Description

CMS HTCondor job history records from the global pool. Each document represents a
completed (or failed/removed) batch job collected from HTCondor schedds via the
CMS Spider monitoring system.

## Document structure

All job data lives under `data.*`. Envelope metadata is under `metadata.*`.

### Time fields

| Field                          | Description                          |
|-------------------------------|--------------------------------------|
| `metadata.timestamp`          | Ingestion timestamp (epoch ms) — **use for time-range filtering** |
| `data.RecordTime`             | Record time from HTCondor (epoch ms) |
| `data.QDate`                  | Job submission time (epoch ms)       |
| `data.JobStartDate`           | Job start time (epoch ms)            |
| `data.CompletionDate`         | Job completion time (epoch ms)       |
| `data.LastMatchTime`          | Last match time (epoch ms)           |
| `data.EnteredCurrentStatus`   | Entered current status (epoch ms)    |

### Job identification

| Field                         | Description                                          |
|-------------------------------|------------------------------------------------------|
| `data.GlobalJobId`            | Unique job ID (e.g. `schedd#cluster.proc#qdate`)      |
| `data.ClusterId`              | HTCondor cluster ID                                   |
| `data.ProcId`                 | HTCondor process ID within the cluster                |
| `data.ScheddName`             | Schedd hostname (e.g. `cmsgwms-submit12.fnal.gov`)    |
| `data.Owner`                  | Unix user that submitted the job                      |
| `data.User`                   | Full user string (e.g. `cmsdataops@cms`)              |

### Job status and exit information

| Field                         | Description / Values                                  |
|-------------------------------|------------------------------------------------------|
| `data.JobStatus`              | HTCondor job status code (4 = Completed, 3 = Removed) |
| `data.Status`                 | Human-readable status (e.g. `Completed`)              |
| `data.ExitCode`               | Job exit code (0 = success)                           |
| `data.ExitBySignal`           | Whether job exited by signal                          |
| `data.ExitStatus`             | Exit status code                                      |
| `data.CondorExitCode`         | HTCondor-level exit code                              |
| `data.ErrorType`              | Error classification (e.g. `Success`)                 |
| `data.ErrorClass`             | Error class (e.g. `Success`)                          |
| `data.JobFailed`              | Whether the job failed (0 or 1)                       |

### CMS-specific fields

| Field                         | Description                                           |
|-------------------------------|-------------------------------------------------------|
| `data.CMS_Pool`               | CMS pool name (e.g. `Global`)                         |
| `data.Type`                   | Job type (e.g. `production`, `analysis`)               |
| `data.CMS_Type`               | CMS job type                                           |
| `data.CMS_JobType`            | CMS job type label (e.g. `Production`, `Analysis`)     |
| `data.CMS_extendedJobType`    | Extended type (e.g. `GEN,SIM,DIGI_premix,RECO,...`)    |
| `data.CMS_RequestType`        | Request type (e.g. `StepChain`, `TaskChain`)           |
| `data.CMS_SubmissionTool`     | Submission tool (e.g. `WMAgent`, `CRAB`)               |
| `data.CMS_WMTool`             | WM tool used                                           |
| `data.CMS_CampaignName`       | Campaign name(s)                                       |
| `data.CMS_CampaignType`       | Campaign type (e.g. `Run3 requests`)                   |
| `data.Campaign`               | Campaign string                                        |
| `data.Workflow`               | Workflow name                                          |
| `data.TaskType`               | Task type(s) (e.g. `GEN,SIM,DIGI_premix,...`)          |
| `data.CMSGroups`              | CMS physics group(s) (array, e.g. `["HIG"]`)          |
| `data.CMSSW_Versions`         | CMSSW versions used                                    |
| `data.WMAgent_RequestName`    | WMAgent request name                                   |
| `data.WMAgent_SubTaskName`    | WMAgent sub-task name                                  |
| `data.WMAgent_TaskType`       | WMAgent task type                                      |
| `data.WMAgent_JobID`          | WMAgent job ID                                         |
| `data.AccountingGroup`        | Accounting group (e.g. `production.cmsdataops`)        |

### Derived metrics — compute client-side

The condor index stores raw fields; **the aggregation tool does NOT support
computed-field filters** (e.g. you cannot write a Lucene query like
`data.RequestMemory/data.RequestCpus:>2200`). For derived metrics, fetch
the component fields and do the math in your final answer, not in the query:

- **memory per core** = `data.RequestMemory_Eval` / `data.RequestCpus`
  - To find jobs above a threshold, aggregate `terms` by `data.Workflow`
    with the `query` filtering only on the components you can express
    directly (e.g. `data.RequestMemory_Eval:>2200`), then filter further
    when rendering the table.
- **per-job wall-clock** = `data.CompletionDate` − `data.JobStartDate`
- **efficiency by core-hour** = aggregate `sum(data.CpuTimeHr)` and
  divide by `sum(data.CoreHr)` in your answer text
- **CPU efficiency** is already pre-computed as `data.CpuEff` (0–100)

If you cannot express the filter with the components directly, retrieve
a top-N sample with the broadest reasonable filter and compute the
threshold in the answer.

### Resource usage

| Field                         | Description                                           |
|-------------------------------|-------------------------------------------------------|
| `data.RequestCpus`            | Number of CPUs requested                               |
| `data.JobCpus`                | Number of CPUs allocated                               |
| `data.CpusProvisioned`        | CPUs provisioned                                       |
| `data.RequestMemory_Eval`     | Requested memory (MB)                                  |
| `data.MemoryProvisioned`      | Memory provisioned (MB)                                |
| `data.MemoryUsage`            | Actual memory usage (MB)                               |
| `data.MemoryMB`               | Peak memory (MB)                                       |
| `data.ResidentSetSize`        | RSS in KB                                              |
| `data.DiskUsage`              | Disk usage (KB)                                        |
| `data.DiskUsageGB`            | Disk usage (GB)                                        |
| `data.RequestDisk`            | Requested disk (KB)                                    |
| `data.RequestGPUs`            | Number of GPUs requested                               |
| `data.GPUsProvisioned`        | GPUs provisioned                                       |

### Time and performance metrics

| Field                         | Description                                           |
|-------------------------------|-------------------------------------------------------|
| `data.WallClockHr`            | Wall-clock time in hours                               |
| `data.CoreHr`                 | Core-hours consumed                                    |
| `data.CommittedCoreHr`        | Committed core-hours                                   |
| `data.CpuTimeHr`             | CPU time in hours                                      |
| `data.CpuEff`                | CPU efficiency (%) — `CpuTimeHr / CoreHr * 100`        |
| `data.CpuBadput`             | Wasted core-hours                                      |
| `data.QueueHrs`              | Time spent in queue (hours)                             |
| `data.RemoteWallClockTime`    | Remote wall-clock time (seconds)                       |
| `data.RemoteUserCpu`          | Remote user CPU time (seconds)                         |
| `data.RemoteSysCpu`           | Remote system CPU time (seconds)                       |
| `data.MaxWallTimeMins`        | Maximum allowed wall time (minutes)                    |

### Site and infrastructure

| Field                              | Description                                      |
|------------------------------------|--------------------------------------------------|
| `data.Site`                        | Execution site (e.g. `T1_FR_CCIN2P3`)             |
| `data.Tier`                        | Site tier (e.g. `T1`, `T2`, `T3`)                  |
| `data.Country`                     | Country code (e.g. `FR`, `US`)                     |
| `data.GLIDEIN_CMSSite`             | GlideinWMS CMS site name                          |
| `data.GLIDEIN_Site`                | GlideinWMS site name                              |
| `data.GLIDEIN_Entry_Name`          | Glidein entry name                                |
| `data.GLIDEIN_SiteWMS`             | Site WMS type (e.g. `HTCondor`)                    |
| `data.GLIDEIN_SiteWMS_Queue`       | CE queue hostname                                 |
| `data.MATCH_EXP_JOBGLIDEIN_ResourceName` | Matched resource name                       |
| `data.MachineAttrCpuModelName0`    | CPU model                                          |
| `data.MachineAttrArch0`            | Architecture (e.g. `X86_64`)                       |
| `data.MachineAttrMicroarch0`       | Microarchitecture (e.g. `x86_64-v4`)               |
| `data.DESIRED_Sites`               | Desired sites list (array)                         |
| `data.InputData`                   | Input data location type (e.g. `Onsite`)           |
| `data.REQUIRED_OS`                 | Required OS (e.g. `rhel8`)                         |

### CMSSW monitoring (Chirp)

| Field                              | Description                                      |
|------------------------------------|--------------------------------------------------|
| `data.ChirpCMSSWRuns`              | Number of CMSSW run steps                         |
| `data.ChirpCMSSWDone`              | Number of completed run steps                     |
| `data.ChirpCMSSWEvents`            | Total events processed                            |
| `data.ChirpCMSSWElapsed`           | Total CMSSW elapsed time (seconds)                |
| `data.ChirpCMSSWTotalCPU`          | Total CMSSW CPU time (seconds)                    |
| `data.ChirpCMSSWReadBytes`         | Total bytes read                                  |
| `data.ChirpCMSSWWriteBytes`        | Total bytes written                               |
| `data.KEvents`                     | Events processed (thousands)                      |
| `data.MegaEvents`                  | Events processed (millions)                       |
| `data.InputGB`                     | Total input data (GB)                             |
| `data.OutputGB`                    | Total output data (GB)                            |
| `data.CMSSWEventRate`              | Overall event rate (events/sec)                   |
| `data.CMSSWTimePerEvent`           | Time per event (seconds)                          |

### Benchmarking

| Field                              | Description                                      |
|------------------------------------|--------------------------------------------------|
| `data.BenchmarkJobHS06`            | HS06 benchmark of the slot                        |
| `data.HS06CoreHr`                  | HS06-normalized core-hours                        |
| `data.HS06CpuTimeHr`              | HS06-normalized CPU time hours                    |
| `data.BenchmarkJobDB12`            | DB12 benchmark                                    |
| `data.DB12CoreHr`                  | DB12-normalized core-hours                        |

### Envelope metadata

| Field                              | Description                                      |
|------------------------------------|--------------------------------------------------|
| `metadata.timestamp`               | Ingestion timestamp (epoch ms)                    |
| `metadata.producer`                | Producer name (`condor`)                           |
| `metadata.topic`                   | Kafka topic (`monit-condor_raw_metric`)            |
| `metadata.type`                    | Record type (`metric`)                             |
| `metadata.type_prefix`             | Type prefix (`raw`)                                |

## Common query patterns

### Find failed jobs at a site
```
data.Site:"T2_US_MIT" AND data.Status:"Failed"
```

### Find production jobs for a campaign
```
data.CMS_JobType:"Production" AND data.Campaign:"RunIII2024*"
```

### Jobs with high memory usage
```
data.MemoryMB:>15000
```

### Jobs by workflow
```
data.Workflow:"HIG-RunIII2024Summer24wmLHEGS-01634"
```

### Jobs with low CPU efficiency
```
data.CpuEff:<50 AND data.Status:"Completed"
```

### Jobs from a specific schedd
```
data.ScheddName:"cmsgwms-submit12.fnal.gov"
```

## Aggregation examples

- Group by `data.Site` to see job distribution across sites.
- Group by `data.Status` to count completed/failed/removed jobs.
- Group by `data.CMS_JobType` to break down by job type.
- Group by `data.ErrorType` to find common error types.
- Use `avg` on `data.CpuEff` with a site filter for average efficiency.
- Use `sum` on `data.CoreHr` to get total core-hours consumed.
- Group by `data.Workflow` to find the busiest workflows.
- Group by `data.Country` to see geographic distribution.
