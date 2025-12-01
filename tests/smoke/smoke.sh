#!/usr/bin/env bash
set -euo pipefail

NAME="${1:-}"
if [[ -z "${NAME}" ]]; then
  echo "Usage: $0 <deployment-name>"
  exit 1
fi

BASE_URL="${BASE_URL:-http://localhost:7861}"
TIMEOUT="${TIMEOUT:-180}"
CLIENT_ID="${CLIENT_ID:-$(python3 - <<'PY'
import uuid
print(uuid.uuid4())
PY
)}"

info() { echo "[smoke] $*"; }

info "Waiting for ${BASE_URL} to be ready (timeout ${TIMEOUT}s)..."
start_ts=$(date +%s)
while true; do
  if curl -fsS "${BASE_URL}/api/health" >/dev/null 2>&1; then
    info "Health OK"
    break
  fi
  now=$(date +%s)
  if (( now - start_ts > TIMEOUT )); then
    echo "Timed out waiting for ${BASE_URL}/api/health" >&2
    exit 1
  fi
  sleep 2
done

set -x
curl -fsS "${BASE_URL}/api/health" | head -c 500
# Test basic connectivity first
curl -fsS "${BASE_URL}/" | head -c 200

# Test chat endpoint with working payload format
if curl -fsS -X POST "${BASE_URL}/api/get_chat_response" \
  -H "Content-Type: application/json" \
  -d '{"last_message":[["User","hello"]],"conversation_id":null,"is_refresh":false,"client_sent_msg_ts":'$(date +%s000)',"client_timeout":300000,"client_id":"'"${CLIENT_ID}"'"}' | head -c 500; then
  info "Chat endpoint test passed"
else
  echo "Chat endpoint check failed" >&2
  exit 1
fi
set +x

info "Smoke tests passed for ${NAME}"
