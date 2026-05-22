#!/bin/bash
# Wait for Qwen35 + aux10 append work to finish, then submit GPT-5.5 270Q.
# Run on orcd-login.
set -euo pipefail

LOG="$HOME/gpt55_after_aux.log"
exec > >(tee -a "$LOG") 2>&1

GPT_OUT_SUBDIR="${GPT_OUT_SUBDIR:-run_270q_gpt55_openai}"
GPT_MODEL="${GPT_MODEL:-gpt-5.5-2026-04-23}"

log() { printf '[%s] %s\n' "$(date -Iseconds)" "$*"; }

wait_clear() {
  while true; do
    local queued
    queued=$(squeue -u "$USER" -h -o "%j" 2>/dev/null | grep -E '^(archi-bench-35b|archi-archive-35b-results|archi-append-aux10-)' || true)
    local proc
    proc=$(pgrep -u "$USER" -f append_aux10_270_orchestrator || true)
    if [ -z "$queued" ] && [ -z "$proc" ]; then
      return 0
    fi
    log "waiting for Qwen35/archive/aux append work to clear"
    squeue -u "$USER" -o "%i %j %T %M %R" | grep -E 'JOBID|archi-bench-35b|archi-archive-35b-results|archi-append-aux10-' || true
    sleep 120
  done
}

validate_qwen270_archives() {
  python3 - <<'PY'
import json
from pathlib import Path

ok = True
for label, base in {
    "35b": Path.home() / "bench_out/run_260q_orcd_v3_35b",
    "27b": Path.home() / "bench_out/run_260q_orcd_v3_27b",
}.items():
    for cfg in ("bare", "rag", "no-tools", "live"):
        path = base / f"results_v3_{cfg}.json"
        if not path.exists():
            print(f"{label} {cfg}: missing {path}")
            ok = False
            continue
        data = json.load(open(path))
        rows = data.get("benchmarking_results", [{}])[0].get("single_question_results", {})
        aux = [f"question_{i}" for i in range(260, 270)]
        missing_aux = [q for q in aux if q not in rows]
        print(f"{label} {cfg}: rows={len(rows)} missing_aux={missing_aux}")
        if len(rows) < 270 or missing_aux:
            ok = False
if not ok:
    raise SystemExit(1)
PY
}

restart_services() {
  log "restarting archi-services before GPT-5.5 full run"
  squeue -u "$USER" -h -o "%i %j" | awk '$2=="archi-services"{print $1}' | while read -r jid; do
    [ -n "$jid" ] && scancel "$jid" || true
  done
  while squeue -u "$USER" -h -o "%j" | grep -q '^archi-services$'; do
    sleep 10
  done
  local jid
  jid=$(sbatch --parsable \
    --export=ALL,ARCHI_BUNDLE="$HOME/archi-deployment-bundle-20260521.tar.zst",ARCHI_AGE_KEY="$HOME/.archi-bundle-key.txt" \
    "$HOME/A2rchi/scripts/slurm/start_archi_services.sh")
  log "archi-services jid=$jid"
  for _ in $(seq 1 80); do
    if [ -f "$HOME/archi-services.env" ] && grep -q "SLURM_JOB_ID=$jid" "$HOME/archi-services.env"; then
      . "$HOME/archi-services.env"
      if curl -fsS -m 10 "$ARCHI_DM_URL/api/catalog/schema" >/dev/null; then
        log "archi-services ready at $ARCHI_DM_URL"
        return 0
      fi
    fi
    sleep 10
  done
  log "ERROR: archi-services did not become ready"
  return 1
}

main() {
  log "GPT-5.5 after-aux orchestrator starting"
  wait_clear
  validate_qwen270_archives
  restart_services
  log "submitting GPT-5.5 270Q chain to $GPT_OUT_SUBDIR"
  ARCHI_OUT_SUBDIR="$GPT_OUT_SUBDIR" \
  ARCHI_OPENAI_MODEL="$GPT_MODEL" \
  ARCHI_LIMIT=270 \
  ARCHI_START=0 \
  ARCHI_QA_CONCURRENCY="${ARCHI_QA_CONCURRENCY:-8}" \
  ARCHI_AGENT_CONCURRENCY="${ARCHI_AGENT_CONCURRENCY:-4}" \
  bash "$HOME/A2rchi/.scratch/launch_gpt55_270_inner.sh"
  log "GPT-5.5 chain submitted"
}

main "$@"
