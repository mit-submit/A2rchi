#!/bin/bash
# Copy OPENAI_API_KEY from local .env into ORCD's archi secret bundle.
# Does not print the secret.
set -euo pipefail

REPO_ROOT="${REPO_ROOT:-/Users/jason/projects/A2rchi}"
LOCAL_ENV="${1:-$REPO_ROOT/.env}"
REMOTE="${2:-orcd-login}"
REMOTE_SECRET="\$HOME/.archi-bundle-state/bundle/secrets/archi/openai_api_key.txt"

[ -f "$LOCAL_ENV" ] || { echo "ERROR: missing $LOCAL_ENV" >&2; exit 2; }

KEY=$(sed -n 's/^OPENAI_API_KEY=//p' "$LOCAL_ENV" | tail -1)
KEY="${KEY%\"}"
KEY="${KEY#\"}"
KEY="${KEY%\'}"
KEY="${KEY#\'}"
[ -n "$KEY" ] || { echo "ERROR: OPENAI_API_KEY not found in $LOCAL_ENV" >&2; exit 3; }

ssh "$REMOTE" "mkdir -p \$(dirname $REMOTE_SECRET); umask 077; cat > $REMOTE_SECRET" <<< "$KEY"
ssh "$REMOTE" "test -s $REMOTE_SECRET && chmod 600 $REMOTE_SECRET"
echo "wrote OPENAI_API_KEY secret to $REMOTE:$REMOTE_SECRET"
