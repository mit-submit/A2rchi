# Overnight 260q bench — resume notes (2026-05-22)

This branch captures the state needed to resume the multi-model overnight bench
that ran into multiple cascading failures. New agent should read this end-to-end
before touching anything.

## Environment

- Branch: `bench/overnight-recovery-snapshot`
- cwd of original session: `/Users/jason/projects/A2rchi`
- Original session transcript:
  `/Users/jason/.claude/projects/-Users-jason-projects-A2rchi/ea8c05f2-ff72-4aeb-84fc-a0da8b2d9bfa.jsonl`
  (the previous compacted session is `fbacee33-…jsonl` in the same dir)
- Cluster: ORCD via ssh alias `orcd-login` (user `mohoney`)
- Other usernames per `~/.claude/CLAUDE.md`: `mohoney@submit75.mit.edu`,
  `mohoney@submit76.mit.edu`, `jmohoney@lxplus.cern.ch`

## TL;DR (results so far)

- **Salvageable**: 35B `bare` (260/260, 1 err), 35B `rag` (260/260, 2 errs)
- **Corrupt**: 35B `no-tools` (89 errs orig, 213 errs rerun), 35B `live` (partial 34/260, 24 errs)
- **Not run yet**: 27B (4 configs), Gemma4-31B (4 configs), Gemma4-26B-A4B (4 configs)
- **Live state on ORCD**: archi-services fresh (jid 14265679); no vllm; orchestrator dead

## Results inventory

Local laptop (under `/Users/jason/projects/A2rchi/bench_out/`):

| Path | Contents |
|---|---|
| `run_260q_orcd_v3_35b/results_v3_bare.json` | ✓ clean (260/260, 1 err, mean 18.6s) |
| `run_260q_orcd_v3_35b/results_v3_rag.json` | ✓ clean (260/260, 2 errs, mean 13.9s, ~12 hits/q) |
| `run_260q_orcd_v3_35b/results_v3_no-tools.json` | ⚠ corrupt original (260/260, 89 errs, mean 352s) |
| `run_260q_orcd_v3_live_inprogress/results_v3_no-tools.json` | ⚠ corrupt rerun (260/260, 213 errs, mean 773s) |
| `run_260q_orcd_v3_live_inprogress/results_v3_live.json` | ⚠ partial rerun (34/260, 24 errs before kill) |

On ORCD (under `~/bench_out/`):

| Path | Contents |
|---|---|
| `run_260q_orcd_v3/` | LIVE results dir; orchestrator clears between sweeps |
| `run_260q_orcd_v3_35b/` | Archived 35B (bare/rag clean; no-tools/live corrupt) |
| (`run_260q_orcd_v3_{27b,gemma4-31b,gemma4-26b}/` will be created during the resumed sweep) |

## On-cluster artifacts

| Path on ORCD | What |
|---|---|
| `~/recovery_orchestrator.sh` | Main overnight orchestrator (35B → 27B → Gemma31 → Gemma26) |
| `~/recovery_orchestrator.log` | Live narrative |
| `~/recovery_orchestrator.log.prev*` | Rotated logs from each restart attempt |
| `~/.recovery_skip_35b` | Sentinel — when present, orchestrator skips steps [2] and [3] for the 35B model |
| `~/archi-services.env` | Endpoints (DM, Postgres, Rucio MCP); written by archi-services |
| `~/archi-vllm.env` | vllm URL + model; written by vllm at startup, **deleted on vllm exit** |
| `~/archi-{services,vllm,bench-*}.*.out` | Per-job slurm output |
| `~/.archi-bundle-state/bundle/secrets/archi/` | Secrets — **no `hf_token.txt` yet** (needed for Gemma) |
| `~/.cache/huggingface/hub/` | HF cache — has Qwen3.6-{35B,35B-FP8}; **no Gemma yet** |

## Root causes of overnight failures

1. **Catalog saturation.** N=32 concurrent agents × multi-turn tool loops → 274+ TCP
   connections to data-manager. Flask data-manager has 8 CPUs (`--cpus-per-task=8`),
   serializes; queue depth runs away. All errors are `per-question timeout 600s`.
2. **Zombie-thread leak.** `asyncio.wait_for(tool.ainvoke(...), timeout=30)` cancels
   the future but can't kill langchain's `to_thread`-wrapped sync `requests.get()`.
   Each timeout leaks a thread + socket → self-DDoS.
3. **State decay.** Same code, same args: original 35B no-tools got 89 errs; rerun
   on a fresh-restarted archi-services after 4h of bench abuse got 213 errs.

## Bugs in the orchestrator (still unfixed; need to fix before relaunching)

1. **27B (dense) crashed at vllm startup**:
   `Number of experts must be > 0 when expert parallelism is enabled`.
   Recovery copy-pasted 35B MoE flags. **Need `enable_ep=0, mtp=0` for Qwen3.6-27B**.
2. **Gemma-4-31B-it** is also dense — same fix.
3. **Gemma-4-26B-A4B-it** is MoE but Qwen MTP isn't portable → `enable_ep=1, mtp=0`.
4. **Orchestrator dies on missing `~/archi-vllm.env`** (vllm exit deletes it, then
   `source` under `set -u` exits with `unbound variable`). Needs guarded source.
5. **HF_TOKEN file missing** — Gemma sweeps require
   `~/.archi-bundle-state/bundle/secrets/archi/hf_token.txt` (gated on HF).

## Fixes already landed in this snapshot

- `recovery_orchestrator.sh`: skip steps [2] and [3] when `~/.recovery_skip_35b` exists
- `recovery_orchestrator.sh`: guards `scancel "$CUR_VLLM_JID"` against empty
- `recovery_orchestrator.sh`: `ARCHI_CONCURRENCY=16` (was 32)
- `scripts/slurm/start_vllm.sh`: `VLLM_REASONING_PARSER`, `VLLM_DISABLE_THINKING` env-configurable
- `.scratch/run_260q_orcd_v3.py`, `.scratch/run_260q_orcd_qa.py`: `os._exit(0)` after `asyncio.run` to prevent zombie-thread hangs

## How to resume the bench

1. Read `.scratch/recovery_orchestrator.sh`. Understand the chain
   (35B → 27B → Gemma-31B → Gemma-26B-A4B).
2. Apply the four orchestrator bug fixes from the section above.
3. Pre-flight checks:
   ```bash
   ssh orcd-login 'squeue -u $USER -o "%i %j %T %M"'
   ssh orcd-login 'tail -30 ~/recovery_orchestrator.log'
   ```
4. Ship + relaunch:
   ```bash
   bash .scratch/skip2_restart.sh
   ```
5. Watch error rate on 27B at N=16 as the canary. If <10% errors → continue Gemma;
   if >30% → drop to N=8 and try again.
6. Pre-Gemma: confirm `~/.archi-bundle-state/bundle/secrets/archi/hf_token.txt`
   exists (orchestrator will skip Gemma cleanly without it).

## Useful one-liners

```bash
# Queue snapshot on ORCD
ssh orcd-login 'squeue -u $USER -o "%i %j %T %M"'

# Orchestrator narrative
ssh orcd-login 'tail -30 ~/recovery_orchestrator.log'

# All sweep counts/errors at once
ssh orcd-login 'for f in ~/bench_out/run_260q_orcd_v3*/*.json; do
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); r=d[\"benchmarking_results\"][0][\"single_question_results\"]; e=sum(1 for v in r.values() if v.get(\"error\")); print(f\"{sys.argv[1]}: {len(r)}/260 errs={e}\")" "$f"
done'

# Pull latest 35B results to laptop
rsync -avz orcd-login:bench_out/run_260q_orcd_v3_35b/ \
            /Users/jason/projects/A2rchi/bench_out/run_260q_orcd_v3_35b/
```
