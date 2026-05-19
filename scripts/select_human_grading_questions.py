#!/usr/bin/env python3
"""Select questions for human grading from the 260-question CompOps set.

Reads `configs/submit76/curated_questions_categorized.json` and writes:
  configs/submit76/curated_questions_human_grading_100.json
  configs/submit76/curated_questions_human_grading_50.json
  configs/submit76/curated_questions_human_grading_audit.csv

Selection criteria, applied in order:
  1. Questions are kept by hand below (KEEP set), based on a read-through
     of all 260. Drops fall into one of: DUP (semantic duplicate of another
     kept question), LIVE (requires live MonIT and is not doc-answerable),
     FRAG (multi-turn fragment that does not stand alone), META (asks
     about archi itself rather than CompOps), VAGUE (low-impact or
     ill-defined).
  2. Each kept question gets an impact score 1-5:
       5 = recurring daily-shift workflow (deploy, reprocess, debug,
           set quota, find logs, create rules)
       4 = high-value factual reference about a core system
       3 = useful documented procedure or definition
       2 = niche but operationally informative
       1 = curiosity / occasional reference
  3. Top-100 = all KEEP entries (there are roughly that many after
     deduplication and filtering).
  4. Top-50 = highest-impact half, balanced across the three categories
     factual_lookup / procedural / exploratory.
"""

import csv
import json
from collections import Counter
from pathlib import Path

DATA = Path("configs/submit76/curated_questions_categorized.json")
OUT_DIR = DATA.parent
OUT_LARGE = OUT_DIR / "curated_questions_human_grading_100.json"
OUT_50  = OUT_DIR / "curated_questions_human_grading_50.json"
AUDIT   = OUT_DIR / "curated_questions_human_grading_audit.csv"

# -----------------------------------------------------------------------------
# Per-question decisions. Index is the 1-based position in the source file.
#
# Each entry is one of:
#   ("KEEP", impact_1_to_5, notes)
#   ("DROP", reason, notes)
#
# Reasons for DROP:
#   DUP  -- semantic duplicate of another KEEP entry
#   LIVE -- requires live MonIT/operational state, not doc-answerable
#   FRAG -- multi-turn conversation fragment, no standalone meaning
#   META -- meta-question about archi itself
#   VAGUE -- too vague, low-impact, or test-instructions baked in
# -----------------------------------------------------------------------------

DECISIONS = {
      1: ("KEEP", 4, "T0 CERN Tape disable date; canonical of a 9-question dup cluster (1,10,22,33,40,46,75,157,215)"),
      2: ("KEEP", 4, "50513 exit-code debugging -- error log content is in the question, doc-judgable"),
      3: ("KEEP", 3, "WMTaskSpace job log debug -- log content in question"),
      4: ("DROP", "DUP",  "kept #3, near-duplicate WMTaskSpace log debug"),
      5: ("KEEP", 4, "find full gridpack file path"),
      6: ("KEEP", 4, "HLTSCOUT/L1SCOUT dataset policy"),
      7: ("DROP", "LIVE", "specific Jira ticket reference (cmstransf-1215)"),
      8: ("DROP", "LIVE", "random FTS link from last 15 min"),
      9: ("DROP", "LIVE", "live transfer counters"),
     10: ("DROP", "DUP",  "kept #1"),
     11: ("DROP", "LIVE", "opensearch live link"),
     12: ("DROP", "VAGUE", "tape deletion campaign with embedded test instruction; kept clean #24"),
     13: ("KEEP", 5, "what is WMAgents -- core system definition"),
     14: ("KEEP", 4, "where to find sandbox wrapper for WMAgent jobs"),
     15: ("KEEP", 4, "Winter 26 campaign status -- long-running, doc-stable"),
     16: ("KEEP", 5, "where to find full logs for a workflow"),
     17: ("KEEP", 3, "DBS2_UNKNOWN_ACQUISION_ERA"),
     18: ("DROP", "LIVE", "latest P&R tickets"),
     19: ("KEEP", 5, "how to access wmagent config"),
     20: ("DROP", "LIVE", "live transfer counters"),
     21: ("KEEP", 4, "TrustPUSitelists definition"),
     22: ("DROP", "DUP",  "kept #1"),
     23: ("DROP", "LIVE", "live transfer anomaly check"),
     24: ("KEEP", 4, "tape deletion 2025 total -- canonical of (12,24,68,74,152-154)"),
     25: ("DROP", "VAGUE", "deep analysis of compops issues -- ill-defined"),
     26: ("DROP", "META",  "are you keeping track of chats"),
     27: ("DROP", "LIVE", "latest P&R ticket"),
     28: ("KEEP", 5, "MINIAOD policy change in 2025"),
     29: ("DROP", "LIVE", "live transfer counters"),
     30: ("KEEP", 5, "how to deploy a WMAgent"),
     31: ("KEEP", 4, "Winter26 issues summary; canonical of (31,34,150,165)"),
     32: ("DROP", "FRAG",  "multi-turn root-cause follow-up"),
     33: ("DROP", "DUP",  "kept #1"),
     34: ("DROP", "DUP",  "kept #31"),
     35: ("KEEP", 3, "link to workflow updater doc"),
     36: ("DROP", "LIVE", "live transfer counters"),
     37: ("DROP", "LIVE", "most common transfer error to fnal disk"),
     38: ("KEEP", 3, "trustpulists for minbias workflows -- specific to a workflow type, stands alone"),
     39: ("DROP", "DUP",  "kept #28"),
     40: ("DROP", "DUP",  "kept #1"),
     41: ("KEEP", 4, "where to find Unified source code"),
     42: ("KEEP", 4, "CMSPROD-368: who/how sets MergedLFNBase -- ingested ticket"),
     43: ("DROP", "FRAG",  "multi-turn"),
     44: ("KEEP", 3, "CMSDM/CMSTRANSF 2025 activity summary -- historical, ingested"),
     45: ("KEEP", 3, "CMSDM ticket range 250-400 topic survey -- ingested, stable"),
     46: ("DROP", "DUP",  "kept #1"),
     47: ("KEEP", 3, "history of conmon service take-over"),
     48: ("DROP", "LIVE", "today's most critical Jira tickets"),
     49: ("KEEP", 4, "explain WMCore code snippet"),
     50: ("KEEP", 4, "CMSTRANSF-1316 -> rucio commands; canonical of (50,147,148,149)"),
     51: ("DROP", "FRAG",  "multi-turn fragment without standalone meaning"),
     52: ("KEEP", 3, "Russian T2 decommissioning -- documented ongoing work"),
     53: ("DROP", "LIVE", "last prod jira ticket -- 'last' is point-in-time"),
     54: ("KEEP", 3, "CMSTRANSF 1059-1400 topic survey -- ingested, stable; canonical of (54,55,63)"),
     55: ("DROP", "DUP",  "kept #54"),
     56: ("KEEP", 5, "CMS data-on-disk policies"),
     57: ("KEEP", 4, "PnR policy explanation"),
     58: ("DROP", "FRAG",  "multi-turn fragment"),
     59: ("KEEP", 4, "what is Storage Manager"),
     60: ("KEEP", 4, "what is Tier0Feeder"),
     61: ("KEEP", 5, "create a new Tier0 release"),
     62: ("KEEP", 4, "CMSSW LogicError debug -- error message is in the question"),
     63: ("DROP", "DUP",  "kept #54"),
     64: ("KEEP", 4, "what are PCL workflows"),
     65: ("KEEP", 3, "when reduce wma_prod copies 2->1"),
     66: ("KEEP", 3, "when FTS overwrite-on-buffer enabled for CMS"),
     67: ("KEEP", 4, "where WMAgent copies unmerged output at a site"),
     68: ("DROP", "DUP",  "kept #24"),
     69: ("KEEP", 5, "what is Tier-0"),
     70: ("KEEP", 5, "deploy Tier-0 replay"),
     71: ("KEEP", 4, "FNAL CTA migration summary"),
     72: ("DROP", "LIVE", "current situation of t0 production node"),
     73: ("DROP", "LIVE", "current era of tier 0"),
     74: ("KEEP", 3, "when was the last deletion campaign"),
     75: ("DROP", "DUP",  "kept #1"),
     76: ("DROP", "LIVE", "TIFR transfer issues now"),
     77: ("DROP", "LIVE", "opensearch last few hours"),
     78: ("DROP", "LIVE", "random FTS job link"),
     79: ("DROP", "LIVE", "random FTS job via opensearch"),
     80: ("KEEP", 3, "gfal2 transfer log analysis -- log content in question"),
     81: ("DROP", "DUP",  "kept #80, near-duplicate gfal2 log"),
     82: ("DROP", "VAGUE", "random user/email from JIRA -- privacy/novelty"),
     83: ("DROP", "DUP",  "kept #82-cluster dropped"),
     84: ("DROP", "DUP",  "kept #82-cluster dropped"),
     85: ("DROP", "DUP",  "kept #82-cluster dropped"),
     86: ("DROP", "DUP",  "kept #82-cluster dropped"),
     87: ("KEEP", 3, "TIFR recommissioning ticket -- documented ongoing work"),
     88: ("DROP", "FRAG",  "fragment 'What about the relval subscriptions?' lacks context"),
     89: ("DROP", "FRAG",  "fragment 'I think there are some issue with the reading tests' lacks context"),
     90: ("KEEP", 4, "production space usage / how to improve resource use"),
     91: ("DROP", "FRAG",  "multi-turn"),
     92: ("DROP", "META",  "configure archi itself"),
     93: ("DROP", "VAGUE", "summarize external Google doc"),
     94: ("KEEP", 3, "CMSHLT-3763 ticket summary -- ingested ticket"),
     95: ("KEEP", 3, "explain a sample weekly P&R report"),
     96: ("DROP", "FRAG",  "multi-turn"),
     97: ("DROP", "FRAG",  "multi-turn"),
     98: ("KEEP", 3, "Continuous Data Deletion -- terminology stands alone"),
     99: ("KEEP", 3, "CMSDM-402 service explanation -- ingested ticket"),
    100: ("DROP", "FRAG",  "fragment 'What are these corrupted / spurious files?' lacks context"),
    101: ("KEEP", 3, "CMSTRANSF-1308 ticket explanation -- ingested ticket"),
    102: ("DROP", "LIVE", "latest job failures"),
    103: ("KEEP", 5, "where to check site readiness/downtime referenced by tickets"),
    104: ("DROP", "LIVE", "recent failed rule example"),
    105: ("DROP", "LIVE", "recent stuck rule example"),
    106: ("DROP", "LIVE", "recent failed transfer example"),
    107: ("KEEP", 4, "how long does a rule stay in replicating state"),
    108: ("KEEP", 4, "rule replicating > 2 weeks without going stuck"),
    109: ("KEEP", 4, "crabschedd vs prodschedd vs tier0schedd"),
    110: ("KEEP", 4, "create rules using local account quota"),
    111: ("DROP", "FRAG",  "multi-turn"),
    112: ("KEEP", 3, "request submission failure debug -- failure context in question"),
    113: ("DROP", "FRAG",  "multi-turn"),
    114: ("KEEP", 4, "who controls staged-to-acquired transition"),
    115: ("KEEP", 3, "explain wmcore data flow page"),
    116: ("DROP", "FRAG",  "multi-turn"),
    117: ("DROP", "FRAG",  "multi-turn"),
    118: ("DROP", "FRAG",  "multi-turn"),
    119: ("KEEP", 3, "compare two failed/successful WF requests -- both request configs in question"),
    120: ("DROP", "DUP",  "kept #119, near-duplicate request comparison"),
    121: ("DROP", "FRAG",  "multi-turn"),
    122: ("DROP", "DUP",  "kept #13"),
    123: ("KEEP", 4, "Unified-WMAgent connection"),
    124: ("KEEP", 3, "are WMAgents controlled by Unified -- stand-alone factual"),
    125: ("KEEP", 3, "WMAgent on vocms machines with Unified daemons -- stand-alone factual"),
    126: ("DROP", "FRAG",  "multi-turn"),
    127: ("DROP", "DUP",  "kept #166"),
    128: ("KEEP", 3, "CMSTZ ticket with named participants -- ingested ticket search"),
    129: ("KEEP", 4, "dataset block name from lumilist via DAS"),
    130: ("DROP", "FRAG",  "multi-turn"),
    131: ("KEEP", 3, "find ticket where stream-to-primary-dataset mapping is discussed"),
    132: ("DROP", "VAGUE", "DAS keys reference paste with unclear ask"),
    133: ("KEEP", 4, "find files in a block matching a lumi range -- stand-alone procedural"),
    134: ("KEEP", 3, "dasgoclient/jq parse error debug -- error in question"),
    135: ("KEEP", 4, "why overwrite not enabled for tape transfers"),
    136: ("KEEP", 4, "lumi ranges in files via DAS"),
    137: ("KEEP", 5, "Production Output activity description (rules, disk/tape destination)"),
    138: ("KEEP", 3, "task TSG-Run3Winter26GS-00002 issue debug -- specific recorded task"),
    139: ("KEEP", 3, "dasgoclient inconsistency debug -- specific dataset/file context in question"),
    140: ("DROP", "META",  "create prompt for another AI agent"),
    141: ("DROP", "META",  "draft a CMSPROD issue title"),
    142: ("KEEP", 3, "L1SCOUT/HLTSCOUT single-replica change -- find the relevant ticket"),
    143: ("KEEP", 5, "Tier-0 reprocess 5 runs from streamers -- exemplar procedural"),
    144: ("KEEP", 4, "rucio probes and how they are used"),
    145: ("KEEP", 3, "monitoring tools developed by CMSDM/CMSTRANSF"),
    146: ("KEEP", 3, "find recent ticket where a block was detached from a dataset in rucio"),
    147: ("DROP", "DUP",  "kept #50 (CMSTRANSF-1316)"),
    148: ("DROP", "DUP",  "kept #50"),
    149: ("DROP", "DUP",  "kept #50"),
    150: ("DROP", "DUP",  "kept #31"),
    151: ("KEEP", 3, "how much data written on tape in 2025 -- historical, stable"),
    152: ("DROP", "DUP",  "kept #24"),
    153: ("DROP", "DUP",  "kept #24"),
    154: ("DROP", "DUP",  "kept #24"),
    155: ("KEEP", 4, "rucio rule details via CLI"),
    156: ("KEEP", 4, "find dataset path at a site after rucio sent it"),
    157: ("DROP", "DUP",  "kept #1"),
    158: ("KEEP", 4, "monte carlo workflows in Tier-0"),
    159: ("KEEP", 4, "why prompt reconstruction not run by central production"),
    160: ("KEEP", 3, "read contents at a specific cmsunified condorlog URL"),
    161: ("DROP", "LIVE", "live transfer counters"),
    162: ("DROP", "LIVE", "live transfer counters"),
    163: ("KEEP", 4, "stage a single raw file from tape"),
    164: ("DROP", "LIVE", "live transfer counters"),
    165: ("DROP", "DUP",  "kept #31"),
    166: ("KEEP", 4, "VO machines where Unified runs"),
    167: ("DROP", "VAGUE", "long log compare without a clear question"),
    168: ("KEEP", 3, "wmcore architecture diagrams"),
    169: ("DROP", "META",  "create skill document for the agent itself"),
    170: ("DROP", "META",  "ask archi to find latest jira ticket"),
    171: ("KEEP", 4, "review two tickets + DataProcessing missing-events log -- root cause analysis"),
    172: ("KEEP", 4, "wmagent training document link"),
    173: ("KEEP", 4, "what does P&R mean in CMS computing"),
    174: ("KEEP", 4, "DAS query for a specific run/dataset"),
    175: ("KEEP", 5, "rucio command to see location of block"),
    176: ("DROP", "LIVE", "live request scan"),
    177: ("DROP", "LIVE", "live failure rate at site"),
    178: ("DROP", "FRAG",  "multi-turn"),
    179: ("DROP", "LIVE", "live CPU efficiency"),
    180: ("DROP", "LIVE", "site-with-highest-failure"),
    181: ("DROP", "LIVE", "site-with-highest-failure"),
    182: ("DROP", "LIVE", "live CPU efficiency analysis"),
    183: ("DROP", "DUP",  "kept #173"),
    184: ("KEEP", 4, "x509 -> OIDC transition status in CMS data management"),
    185: ("KEEP", 4, "when did we stop sending Monte Carlo to CERN tape"),
    186: ("KEEP", 5, "how to check the integrity of a file"),
    187: ("KEEP", 3, "cmsunified service-status board missing-config error -- error in question"),
    188: ("KEEP", 5, "why a rule may get stuck in INJECT state"),
    189: ("KEEP", 5, "extend a user's rucio quota at a site"),
    190: ("KEEP", 4, "default rucio quota at a site"),
    191: ("DROP", "LIVE", "live transfer counters"),
    192: ("DROP", "LIVE", "live request scan"),
    193: ("KEEP", 4, "primary use of wma_prod and wmcore_output rucio accounts"),
    194: ("KEEP", 3, "TIFR transfer-failure investigation -- methodology question"),
    195: ("DROP", "LIVE", "current top-failure site"),
    196: ("DROP", "LIVE", "current top-failure site for jobs"),
    197: ("DROP", "LIVE", "current most-common error"),
    198: ("DROP", "LIVE", "FNAL CPU efficiency on a specific date"),
    199: ("DROP", "META",  "are you working on my previous request"),
    200: ("KEEP", 3, "CMSPROD-374 -- explain what changes and why"),
    201: ("DROP", "VAGUE", "compops laser tag joke"),
    202: ("DROP", "FRAG",  "multi-turn"),
    203: ("KEEP", 3, "when RAL Tape moved to CTA"),
    204: ("DROP", "FRAG",  "multi-turn"),
    205: ("KEEP", 4, "which service deletes unmerged files at T1/T2"),
    206: ("KEEP", 4, "how msunmerged works and open issues"),
    207: ("DROP", "LIVE", "current available space scan"),
    208: ("DROP", "FRAG",  "multi-turn"),
    209: ("DROP", "LIVE", "live core count at site"),
    210: ("DROP", "FRAG",  "multi-turn"),
    211: ("KEEP", 5, "how to run a Tier-0 job interactively"),
    212: ("DROP", "LIVE", "live deletion-failed events"),
    213: ("KEEP", 3, "explain how evictions/preemptions contributed -- documented behaviour"),
    214: ("DROP", "VAGUE", "mechanical URL parameter manipulation"),
    215: ("DROP", "DUP",  "kept #1"),
    216: ("KEEP", 4, "CRAB user jobs idle/unsubmitted -- diagnosis methodology; canonical of (216,222,223)"),
    217: ("DROP", "FRAG",  "multi-turn"),
    218: ("KEEP", 5, "rucio command to create a container"),
    219: ("DROP", "DUP",  "kept #218"),
    220: ("KEEP", 5, "rucio CLI command to create a rule"),
    221: ("KEEP", 4, "rucio CLI command compatible with new structure (client v38)"),
    222: ("DROP", "DUP",  "kept #216"),
    223: ("DROP", "DUP",  "kept #216"),
    224: ("KEEP", 5, "where to find logs of failed production jobs"),
    225: ("KEEP", 3, "who fills EOS quota JSON files in /eos/cms/store/accounting/"),
    226: ("KEEP", 3, "task_HIN-pPb816Spring16GS-00222 stuck in acquired -- specific recorded task"),
    227: ("DROP", "LIVE", "live failure rate"),
    228: ("DROP", "LIVE", "live failure-cause investigation"),
    229: ("DROP", "LIVE", "current top-failure sites"),
    230: ("DROP", "FRAG",  "multi-turn"),
    231: ("KEEP", 3, "exit code 8901 at Purdue site vs workflow analysis -- methodology"),
    232: ("KEEP", 3, "CMSTRANSF-1323 best course of action -- ingested ticket"),
    233: ("DROP", "LIVE", "live MINI replication scan"),
    234: ("DROP", "LIVE", "live Purdue failure investigation"),
    235: ("DROP", "LIVE", "live failed transfer example"),
    236: ("KEEP", 4, "how to submit job to grid"),
    237: ("DROP", "LIVE", "live opensearch ownership lookup"),
    238: ("KEEP", 3, "what eos instance serves /eos/project-c"),
    239: ("KEEP", 3, "who cleans /eos/cms/store/logs/prod/recent/PRODUCTION"),
    240: ("KEEP", 3, "owner of monit_prod_cms_eos_mon_raw_metric data source"),
    241: ("DROP", "LIVE", "important tickets last 2 days"),
    242: ("KEEP", 4, "how Tier-0 protects data on disk"),
    243: ("KEEP", 3, "T3_US_NERSC site information"),
    244: ("DROP", "FRAG",  "multi-turn"),
    245: ("KEEP", 4, "where prod job logs are kept"),
    246: ("KEEP", 4, "PnR Campaign manager partial_copy parameter"),
    247: ("KEEP", 3, "analyse partial_copy across campaigns -- campaign config in question"),
    248: ("KEEP", 3, "CMSTRANSF-1322 -- what to do to solve it"),
    249: ("DROP", "FRAG",  "multi-turn"),
    250: ("DROP", "FRAG",  "multi-turn"),
    251: ("DROP", "FRAG",  "multi-turn"),
    252: ("KEEP", 3, "rucio rule script for EGamma EcalUncalibSkim -- script in question"),
    253: ("KEEP", 3, "CMSPROD-308 historical comparison -- what changed since last year"),
    254: ("KEEP", 4, "PromptReco timing relative to run start/stop at Tier-0"),
    255: ("KEEP", 3, "ExitStatus 8901 UnexpectedJobTermination XML -- error code lookup"),
    256: ("KEEP", 5, "set rucio quota for an account in a given RSE"),
    257: ("DROP", "LIVE", "live production-vs-analysis fraction"),
    258: ("KEEP", 4, "how to read a pkl file of a job log"),
    259: ("KEEP", 4, "DAS dataset 75% block completion -- find missing blocks"),
    260: ("DROP", "LIVE", "PnR Jira summary last 3 days"),
}


def main() -> None:
    with DATA.open() as f:
        qs = json.load(f)

    assert len(qs) == 260, f"expected 260 questions, got {len(qs)}"
    assert sorted(DECISIONS.keys()) == list(range(1, 261)), "decision map must cover 1..260"

    keeps = []
    audit_rows = []
    drop_reason_counts = Counter()
    for i, q in enumerate(qs, start=1):
        decision = DECISIONS[i]
        verdict = decision[0]
        cat = q.get("category") or ""
        ans = "doc" if q.get("answerable_from_docs") else "live"
        text = q["question"].strip()
        if verdict == "KEEP":
            impact = decision[1]
            note = decision[2]
            keeps.append((i, q, impact, note))
            audit_rows.append((i, "KEEP", impact, "", cat, ans, note,
                               text[:140].replace("\n", " ")))
        else:
            reason = decision[1]
            note = decision[2]
            drop_reason_counts[reason] += 1
            audit_rows.append((i, "DROP", "", reason, cat, ans, note,
                               text[:140].replace("\n", " ")))

    print(f"KEEP : {len(keeps)} of 260")
    print(f"DROP : {sum(drop_reason_counts.values())}")
    for reason, n in drop_reason_counts.most_common():
        print(f"  {reason:>5s}: {n}")

    by_cat = Counter(q["category"] for _, q, _, _ in keeps)
    print()
    print("KEEP by category:")
    for c, n in by_cat.most_common():
        print(f"  {c:22s} {n}")

    by_impact = Counter(impact for _, _, impact, _ in keeps)
    print()
    print("KEEP by impact:")
    for imp in sorted(by_impact.keys(), reverse=True):
        print(f"  impact {imp}: {by_impact[imp]}")

    # ---- Top 100: every KEEP, ordered by (impact desc, original index). ----
    # Top-100: balance roughly across categories using larger targets, then
    # fill any remaining slots with highest-impact items not yet picked.
    keeps_sorted = sorted(keeps, key=lambda r: (-r[2], r[0]))
    cat_target_100 = {
        "factual_lookup": 50,
        "procedural":     28,
        "exploratory":    22,
    }
    seen100 = Counter()
    top_large = []
    for i, q, impact, note in keeps_sorted:
        c = q["category"]
        if seen100[c] < cat_target_100.get(c, 0):
            top_large.append((i, q, impact, note))
            seen100[c] += 1
        if len(top_large) == 100:
            break
    if len(top_large) < 100:
        chosen = {r[0] for r in top_large}
        for i, q, impact, note in keeps_sorted:
            if i in chosen:
                continue
            top_large.append((i, q, impact, note))
            if len(top_large) == 100:
                break
    OUT_LARGE.write_text(json.dumps([k[1] for k in top_large], indent=2))
    print(f"\nwrote {OUT_LARGE} ({len(top_large)} questions)")
    print("Top-100 by category:")
    for c, n in Counter(q["category"] for _, q, _, _ in top_large).most_common():
        print(f"  {c:22s} {n}")

    # ---- Top 50: balance roughly across categories, impact-first. ----
    # Aim for ~ 25 factual_lookup, ~15 procedural, ~5 exploratory, ~5 other
    # to mirror the operator workload while preferring high-impact items.
    cat_target = {
        "factual_lookup":      18,
        "procedural":          16,
        "exploratory":          6,
        "jira_investigation":   5,
        "debugging":            4,
        "data_query":           1,
    }
    seen = Counter()
    top50 = []
    # First pass: respect per-category targets, top-impact first.
    for i, q, impact, note in keeps_sorted:
        c = q["category"]
        if seen[c] < cat_target.get(c, 0):
            top50.append((i, q, impact, note))
            seen[c] += 1
        if len(top50) == 50:
            break
    # Second pass: any remaining slots go to highest-impact items not yet picked.
    if len(top50) < 50:
        chosen = {r[0] for r in top50}
        for i, q, impact, note in keeps_sorted:
            if i in chosen:
                continue
            top50.append((i, q, impact, note))
            if len(top50) == 50:
                break

    OUT_50.write_text(json.dumps([k[1] for k in top50], indent=2))
    top50_idx = {r[0] for r in top50}
    print(f"wrote {OUT_50} ({len(top50)} questions)")

    print("\nTop-50 by category:")
    for c, n in Counter(q["category"] for _, q, _, _ in top50).most_common():
        print(f"  {c:22s} {n}")

    # ---- Audit CSV: every question with verdict, reason, impact, note. ----
    with AUDIT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["idx", "verdict", "impact", "drop_reason",
                    "category", "answerable_from", "in_top_large", "in_top50",
                    "note", "question_preview"])
        top_large_idx = {r[0] for r in top_large}
        for row in audit_rows:
            idx = row[0]
            extra_in100 = "1" if idx in top_large_idx else ""
            extra_in50  = "1" if idx in top50_idx  else ""
            w.writerow([row[0], row[1], row[2], row[3],
                        row[4], row[5], extra_in100, extra_in50,
                        row[6], row[7]])
    print(f"wrote {AUDIT}")


if __name__ == "__main__":
    main()
