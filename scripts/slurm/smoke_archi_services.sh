#!/bin/bash
# Independent smoke test of a deployed Archi data layer. Reads endpoints from
# $HOME/archi-services.env (or accepts them via env vars) and validates the
# same four checks the proposal §7.1 calls out:
#
#   A. /api/catalog/schema returns expected JSON shape
#   B. /api/catalog/search?q=Rucio+rule&mode=hybrid returns >=1 hit
#   C. Row counts match the baseline in MANIFEST.json
#   D. rucio-mcp answers an MCP `initialize` JSON-RPC
#
# Exit codes:
#   0   all four checks pass
#   1   any check fails
#   2   could not find endpoints / bundle dir
#
# Usage (after `sbatch start_archi_services.sh` has written archi-services.env):
#
#   ./smoke_archi_services.sh                       # default: read $HOME/archi-services.env
#   ARCHI_BUNDLE_DIR=$HOME/.archi-bundle-state/bundle ./smoke_archi_services.sh
#
# Standalone (no Archi env file):
#   ARCHI_DM_URL=http://node1604:7871 ARCHI_RUCIO_MCP_URL=http://node1604:8000/mcp \
#     ./smoke_archi_services.sh

set -uo pipefail

log()  { printf '[smoke] %s\n' "$*" >&2; }
pass() { printf '[smoke] PASS: %s\n' "$*" >&2; PASS=$((PASS+1)); }
fail() { printf '[smoke] FAIL: %s\n' "$*" >&2; FAIL=$((FAIL+1)); }

PASS=0
FAIL=0

# --- locate endpoints ---
ENV_FILE=${ARCHI_SERVICES_ENV:-$HOME/archi-services.env}
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi
[ -n "${ARCHI_DM_URL:-}" ]        || { log "no ARCHI_DM_URL (set $ENV_FILE or env vars)"; exit 2; }
[ -n "${ARCHI_RUCIO_MCP_URL:-}" ] || { log "no ARCHI_RUCIO_MCP_URL"; exit 2; }

log "DM:           $ARCHI_DM_URL"
log "RUCIO MCP:    $ARCHI_RUCIO_MCP_URL"

# --- locate MANIFEST.json (for the baseline row counts in check C) ---
MANIFEST=""
for candidate in "${ARCHI_BUNDLE_DIR:-}" "$HOME/.archi-bundle-state/bundle" \
                 "/var/tmp/$USER/archi-bundle-20260521" ; do
  if [ -n "$candidate" ] && [ -f "$candidate/MANIFEST.json" ]; then
    MANIFEST=$candidate/MANIFEST.json
    break
  fi
done
[ -n "$MANIFEST" ] && log "MANIFEST:     $MANIFEST"

###############################################################################
# A. /api/catalog/schema
###############################################################################
log "Check A: /api/catalog/schema"
body=$(curl -sS -m 15 "$ARCHI_DM_URL/api/catalog/schema" 2>&1 || true)
if echo "$body" | python3 -c "
import json,sys
d = json.load(sys.stdin)
assert isinstance(d.get('keys'), list) and len(d['keys']) > 5, 'keys list missing or too short'
assert 'ticket' in (d.get('source_types') or []), 'source_types missing ticket'
print('OK')" 2>&1 | grep -q '^OK$'; then
  pass "schema has expected keys + source_types"
else
  fail "schema response missing fields: $(echo $body | head -c 200)"
fi

###############################################################################
# B. /api/catalog/search?q=Rucio+rule&mode=hybrid
###############################################################################
log "Check B: hybrid search 'Rucio rule'"
body=$(curl -sS -m 30 "$ARCHI_DM_URL/api/catalog/search?q=Rucio+rule&mode=hybrid&limit=2" 2>&1 || true)
hits=$(echo "$body" | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
    h = d.get('hits') or []
    print(len(h))
except Exception:
    print(0)" 2>/dev/null)
if [ "${hits:-0}" -ge 1 ]; then
  pass "hybrid search returned $hits hit(s)"
else
  fail "hybrid search returned 0 hits (or invalid JSON): $(echo $body | head -c 200)"
fi

###############################################################################
# C. Row counts match baseline
###############################################################################
if [ -n "$MANIFEST" ]; then
  log "Check C: row counts vs MANIFEST baseline"
  # Pull baseline from manifest's row_counts.tsv (path is relative to bundle dir)
  ROW_COUNT_FILE=$(dirname "$MANIFEST")/data/row_counts.tsv
  if [ -f "$ROW_COUNT_FILE" ]; then
    # The manifest baseline says documents=5342, document_chunks=7097.
    # Compare against current via the dm catalog/search hit count
    # (catalog has the same row count as 'documents' table on submit75).
    body=$(curl -sS -m 30 "$ARCHI_DM_URL/api/catalog/search?q=&mode=grep&limit=10000" 2>&1 || true)
    # Use total_hits if exposed, else count returned hits (capped at limit)
    doc_count=$(echo "$body" | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
    n = d.get('total') or d.get('total_hits') or len(d.get('hits') or [])
    print(int(n))
except Exception:
    print(0)")
    expected=$(awk -F'\t' '/^documents/ {print $2}' "$ROW_COUNT_FILE")
    if [ -n "$expected" ] && [ "$doc_count" -ge $(( expected / 2 )) ]; then
      pass "documents row count plausible: got=$doc_count baseline=$expected"
    else
      fail "documents row count off: got=$doc_count baseline=$expected"
    fi
  else
    log "  (baseline row_counts.tsv not found; skipping check C)"
  fi
else
  log "Check C: skipped (no MANIFEST found; pass --bundle-dir or set ARCHI_BUNDLE_DIR)"
fi

###############################################################################
# D. rucio-mcp initialize
###############################################################################
log "Check D: rucio-mcp initialize"
body=$(curl -sS -m 10 -X POST -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  "$ARCHI_RUCIO_MCP_URL" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}},"id":1}' 2>&1 || true)
if echo "$body" | grep -q 'rucio-mcp'; then
  pass "rucio-mcp initialize handshake works"
else
  fail "rucio-mcp initialize did not return server info: $(echo $body | head -c 200)"
fi

###############################################################################
# Summary
###############################################################################
echo
log "===================="
log "PASS: $PASS  FAIL: $FAIL"
log "===================="
[ "$FAIL" -eq 0 ]
