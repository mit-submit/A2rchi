#!/bin/bash
#SBATCH --job-name=archi-test
#SBATCH --output=archi-services.%j.out
#SBATCH --error=archi-services.%j.out
#SBATCH --time=00:15:00                    # mit_quicktest hard cap
#SBATCH --cpus-per-task=2
#SBATCH --mem=8G
#SBATCH --partition=mit_quicktest
#SBATCH --nodes=1

# Same as start_archi_services.sh but on mit_quicktest with minimal footprint.
# Use this for iterating on deploy bugs; switch to start_archi_services.sh
# once the deploy is known-good.

set -euo pipefail

log()  { printf '[start_archi] %s\n' "$*" >&2; }
die()  { log "ERROR: $*"; exit 1; }

if command -v module >/dev/null 2>&1; then
  module load apptainer/1.4.2 2>/dev/null || module load apptainer 2>/dev/null || true
fi

[ -n "${ARCHI_BUNDLE:-}" ]   || die "ARCHI_BUNDLE env var required"
[ -n "${ARCHI_AGE_KEY:-}" ]  || die "ARCHI_AGE_KEY env var required"
[ -e "$ARCHI_BUNDLE" ]       || die "ARCHI_BUNDLE=$ARCHI_BUNDLE not found"
[ -f "$ARCHI_AGE_KEY" ]      || die "ARCHI_AGE_KEY=$ARCHI_AGE_KEY not found"

export PG_PORT=${PG_PORT:-5436}
export DM_PORT=${DM_PORT:-7871}
export RUCIO_PORT=${RUCIO_PORT:-8000}
export ARCHI_BIND_HOST=$(hostname)
export ARCHI_PERSISTENT=${ARCHI_PERSISTENT:-$HOME/.archi-bundle-state}
mkdir -p "$ARCHI_PERSISTENT"

log "Host:        $(hostname)"
log "Job:         $SLURM_JOB_ID"
log "Persistent:  $ARCHI_PERSISTENT"

BUNDLE_DIR=$ARCHI_PERSISTENT/bundle
[ -f "$BUNDLE_DIR/.extracted" ] || die "extracted bundle missing at $BUNDLE_DIR"
log "Bundle dir:  $BUNDLE_DIR"

cd "$BUNDLE_DIR"
./deploy_apptainer.sh

# Quick smoke right here so we don't have to chain another sbatch
log "Quick smoke (curl /api/catalog/schema):"
curl -sS -m 10 "http://${ARCHI_BIND_HOST}:${DM_PORT}/api/catalog/schema" | head -c 200
echo

log "Stack up. Tearing down at end of allocation."
{
  echo "SLURM_JOB_ID=$SLURM_JOB_ID"
  cat endpoints.env
} > "$HOME/archi-services.env"

# In a 15-min test job, just hold briefly then exit so the next iteration
# can start fresh. The persistent state remains on $HOME for the next run.
trap '"$BUNDLE_DIR/deploy_apptainer.sh" --teardown; rm -f "$HOME/archi-services.env"' EXIT
sleep 60  # let the user (me) curl from login during this minute
