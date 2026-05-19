#!/usr/bin/env python3
"""Rank the top-100 human-grading questions by 'interest' for shareable review.

Reads `configs/submit76/curated_questions_human_grading_100.json` and writes
a ranked markdown file the user can share with a CompOps collaborator:

  configs/submit76/curated_questions_human_grading_100_ranked.md

Interest is a hand-assigned 1-10 score that captures how revealing each
question is for evaluating an LLM operator agent. It is deliberately
distinct from the 'impact' score in the selection step (which captures
how often an operator hits the question in their daily workflow).

Rubric:
  10  -- substantial multi-step diagnostic / realistic operator scenario
         with rich context (logs, configs, task IDs) embedded in the
         question
  9   -- a strong specific-ticket investigation with required output
         (rucio commands, root cause), or a non-trivial debug methodology
  7-8 -- specific named ticket / artifact lookup, or a substantial
         procedural with steps to follow
  5-6 -- solid factual reference about a core system, or a standard
         procedural lookup
  3-4 -- date, link, or simple definition lookup
"""

import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC  = ROOT / "configs/submit76/curated_questions_categorized.json"
TOP  = ROOT / "configs/submit76/curated_questions_human_grading_100.json"
OUT  = ROOT / "configs/submit76/curated_questions_human_grading_100_ranked.json"


# Interest scores keyed by 1-based index in the source dataset.
# Only the indices in the top-100 file are used.
INTEREST = {
    # --- Tier S: substantial diagnostic / multi-step / realistic ---------
      2: (10, "Multi-site 50513 exit code with full SCRAM error log embedded"),
     62: (10, "Specific CMSSW LogicError; agent has to find the root cause in the CMSSW repo"),
    143: (10, "Tier-0 operator: 'I made a mistake, need to reprocess 5 runs'; complete recovery scenario"),
    171: (10, "Two specific tickets plus a DataProcessing missing-events log; root-cause analysis"),
    216: (10, "Real CRAB-user help ticket: 'jobs stuck in idle/unsubmitted for days'"),
    194: (9, "TIFR transfer-failure investigation; tests methodology, not just lookup"),
    119: (9, "Compare two workflow requests, one fails one succeeds; both configs in the question"),
    112: (9, "Workflow submission failure with full ConfigCacheID context"),
    189: (9, "Extend a user's rucio quota at T2_CN_Beijing; realistic site-admin question"),
    156: (9, "datasets sent to T2_US_MIT; find their actual file path on disk"),
    226: (9, "task_HIN-pPb816Spring16GS-00222 stuck in acquired for weeks; concrete debug"),
    231: (9, "Exit code 8901 at Purdue: site issue or workflow issue?"),

    # --- Tier A: specific-ticket investigations or substantial procedurals
     50: (8, "CMSTRANSF-1316 -> rucio commands to enact the ticket"),
     42: (8, "CMSPROD-368: who/how sets MergedLFNBase correctly"),
    232: (8, "CMSTRANSF-1323: best course of action"),
    248: (8, "CMSTRANSF-1322: how to solve it"),
    253: (8, "CMSPROD-308 historical: what changed since last year that caused this"),
    138: (8, "Specific task TSG-Run3Winter26GS-00002 debug"),
    134: (8, "Specific dasgoclient/jq parse error; full command in question"),
    187: (8, "cmsunified service-status board missing-config error; full traceback"),
    160: (8, "Read contents at a specific cmsunified condorlog URL"),
    200: (8, "CMSPROD-374: explain what changes and why"),
     99: (8, "CMSDM-402: explain what service is being created and why"),
    101: (7, "CMSTRANSF-1308 ticket explanation"),
     94: (7, "CMSHLT-3763 ticket summary"),
    128: (7, "Find the CMSTZ ticket where two specific people both participated"),
    131: (7, "Find the ticket where stream-to-primary-dataset mapping is discussed"),
    142: (7, "L1SCOUT/HLTSCOUT single-replica change: find the relevant ticket"),
    146: (7, "Find recent ticket where a block was detached from a dataset in rucio"),
     61: (7, "Create a new Tier-0 release"),
     70: (7, "Deploy a Tier-0 replay"),
     30: (7, "Deploy a WMAgent"),
    110: (7, "Create rucio rules using local account quota at T2_CH_CERN"),
    218: (7, "Create a container in rucio: full CLI command"),
    220: (7, "Create a rule in rucio: full CLI command"),
    256: (7, "Set rucio quota for an account in a given RSE"),
    221: (7, "Rucio CLI for new client v38 structure"),
    133: (7, "Given a block, find files matching a lumi range"),
    136: (7, "Given a list of files, get the lumi ranges in them via DAS"),
    129: (7, "Get a dataset block name from a lumilist via DAS"),
    174: (7, "Provide DAS query for run 393974 in /IonPhysics16/pORun2025-v1/RAW"),
    175: (7, "Rucio command to see the location of a block"),
    259: (7, "DAS dataset block completion 75%: find the missing blocks"),
     95: (7, "Explain a real weekly P&R report; tests operational vocabulary"),
     87: (7, "TIFR recommissioning recent ticket"),
     52: (7, "Russian T2 decommissioning open tickets"),
     45: (7, "Topic survey across CMSDM tickets 250-400"),
     54: (7, "Topic survey across CMSTRANSF tickets 1059-1400"),
     44: (7, "CMSDM/CMSTRANSF 2025 yearly activity summary"),
     49: (7, "Explain what a specific WMCore code snippet does (with GitHub URL)"),
     80: (7, "Analyse a specific gfal2 transfer log; full log in question"),
      3: (7, "Analyse a long WMTaskSpace job log"),
    155: (7, "Rucio rule details via CLI"),
    247: (7, "Analyse partial_copy across a config of campaigns; full JSON in question"),
    252: (7, "Rucio rule script for EGamma EcalUncalibSkim; full bash in question"),

    # --- Tier B: solid factual / standard procedural ---------------------
    137: (6, "Production Output activity description: how rules are created, disk vs tape destination"),
     16: (6, "Where to find full logs for a workflow"),
     19: (6, "How to access wmagent config"),
      5: (6, "How to find the full gridpack file path"),
    186: (6, "How to check the integrity of a file"),
    211: (6, "How to run a Tier-0 job interactively"),
    103: (6, "Where to check site readiness and downtime referenced by tickets"),
    188: (6, "Why a rule may get stuck in INJECT state"),
    144: (6, "What rucio probes are and how they monitor"),
    109: (6, "crabschedd vs prodschedd vs tier0schedd: differences"),
    107: (5, "How long does a rule stay in replicating state"),
    108: (6, "Cases where a rule stays > 2 weeks in replicating without going stuck"),
    114: (6, "Who controls the staged-to-acquired transition (and how it can fail)"),
    205: (6, "Unmerged files at RAL T1/T2: which service deletes them, monitoring"),
    206: (6, "How msunmerged works and any open issues"),
    224: (6, "Where to find logs of failed production jobs"),
    245: (6, "Where prod job logs are kept"),
    258: (6, "How to read a pkl file of a job log"),
    236: (6, "How to submit a job to the grid"),
     31: (6, "Summarize the latest mess with the Winter26 workflows"),
     15: (6, "Status of the Winter 26 campaign"),
     57: (6, "Explain the current PnR policy"),
    246: (6, "PnR Campaign manager partial_copy parameter"),

     13: (5, "What is WMAgents (core system definition)"),
     69: (5, "Hello, what is Tier-0"),
     56: (5, "Current CMS policies for keeping data on Disks"),
     59: (5, "What is Storage Manager"),
     60: (5, "What is the Tier0Feeder"),
     64: (5, "What are PCL workflows"),
     67: (5, "Where WMAgent copies unmerged output at a given site"),
    123: (5, "How Unified and WMAgent are connected"),
    166: (5, "In which VO machines is Unified running"),
    158: (5, "Monte Carlo workflows in Tier-0"),
    159: (5, "Why prompt reconstruction is not run by central production"),
    163: (5, "Is it possible to stage a single raw file from tape"),
    173: (5, "What does P&R mean in CMS computing"),
    135: (5, "Why overwrite is not enabled for tape transfers"),
      6: (5, "HLTSCOUT/L1SCOUT dataset policy"),
     21: (5, "TrustPUSitelists definition for classical pileup mixing"),
     38: (5, "TrustPUSitelists value specifically for minbias workflows"),
     28: (5, "Summarize the MINIAOD policy change in 2025"),
     14: (5, "Where to find the sandbox wrapper for WMAgent jobs"),
     41: (5, "Where to find Unified source code"),
     71: (5, "Summary of the FNAL CTA migration"),
    115: (5, "Explain the wmcore data flow training page"),
    190: (5, "Default rucio quota for users at a specific site"),
    193: (5, "Primary use of wma_prod and wmcore_output rucio accounts"),
    242: (5, "How Tier-0 protects data on disk"),
     98: (5, "What is Continuous Data Deletion"),
    145: (5, "Monitoring tools developed by CMSDM/CMSTRANSF in the past year"),
     90: (5, "Production space usage: how to improve resource use"),

    # --- Tier C: date / location / simple-definition lookups -------------
      1: (4, "When was T0_CH_CERN_Tape disabled for production output?"),
     17: (4, "What is DBS2_UNKNOWN_ACQUISION_ERA?"),
     35: (4, "Link to the workflow updater doc"),
     65: (4, "When did wma_prod copies go from 2 to 1?"),
     66: (4, "When did FTS enable overwrite-on-buffer for CMS?"),
     74: (4, "When was the last deletion campaign?"),
    168: (4, "Find the wmcore architecture diagrams"),
    172: (4, "Link to the wmagent training document"),
    184: (4, "Status of x509 -> OIDC transition in CMS data management"),
    185: (4, "When did we stop sending Monte Carlo to CERN tape?"),
    254: (4, "At Tier-0, is PromptReco released when the run starts or stops?"),
     47: (4, "History of the conmon service take-over"),
     24: (4, "How much data was deleted in the 2025 deletion campaign?"),
    124: (4, "Are WMAgents controlled by Unified?"),
    125: (4, "Are WMAgent vocms machines where Unified daemons run?"),
    151: (4, "How much data was written on tape in 2025?"),
    225: (4, "Who fills the EOS quota JSON files in /eos/cms/store/accounting/?"),
    238: (4, "Which EOS instance serves /eos/project-c"),
    239: (4, "Who cleans /eos/cms/store/logs/prod/recent/PRODUCTION"),
    240: (4, "Owner of monit_prod_cms_eos_mon_raw_metric data source"),
}


CAT_SHORT = {
    "factual_lookup":     "factual",
    "procedural":         "procedural",
    "exploratory":        "exploratory",
    "jira_investigation": "jira",
    "debugging":          "debugging",
    "data_query":         "data-query",
}


def main() -> None:
    with SRC.open() as f:
        src = json.load(f)
    with TOP.open() as f:
        top = json.load(f)

    text_to_idx = {q["question"].strip(): i + 1 for i, q in enumerate(src)}

    rows = []
    missing = []
    for q in top:
        idx = text_to_idx[q["question"].strip()]
        if idx not in INTEREST:
            missing.append(idx)
            continue
        score, why = INTEREST[idx]
        rows.append((idx, score, why, q))

    if missing:
        raise SystemExit(
            f"missing interest scores for indices: {missing[:20]}{'...' if len(missing) > 20 else ''}"
        )

    # Sort by interest score (high to low), then by index (stable).
    rows.sort(key=lambda r: (-r[1], r[0]))

    score_counts = Counter(r[1] for r in rows)
    print("Interest score distribution (top-100):")
    for s in sorted(score_counts, reverse=True):
        bar = "#" * score_counts[s]
        print(f"  {s:>2}: {score_counts[s]:>3}  {bar}")

    # ---- JSON output ----------------------------------------------------
    out_rows = []
    for rank, (idx, score, why, q) in enumerate(rows, start=1):
        out_rows.append({
            "rank":                 rank,
            "interest":             score,
            "idx":                  idx,
            "category":             q["category"],
            "answerable_from_docs": bool(q.get("answerable_from_docs")),
            "multi_turn":           bool(q.get("multi_turn")),
            "time_sensitive":       bool(q.get("time_sensitive")),
            "why_interesting":      why,
            "question":             q["question"].strip(),
            "reference_answer":     q.get("reference_answer"),
            "reference_source":     q.get("reference_source"),
        })
    OUT.write_text(json.dumps(out_rows, indent=2, ensure_ascii=False))
    print(f"\nwrote {OUT}")
    print(f"  {len(out_rows)} ranked entries")


if __name__ == "__main__":
    main()
