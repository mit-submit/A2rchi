#!/bin/bash
# Try search_content=true with various queries to find one that works for full sentences.
ssh orcd-login bash <<'REMOTE'
TESTS=(
  'q=test|limit=5'
  'q=SCRAMScriptFailure|search_content=true|limit=5'
  'q=T0_CH_CERN_Tape|search_content=true|limit=5'
  'q=When was T0_CH_CERN_Tape disabled|search_content=true|limit=5'
  'q=tier-0 tape|search_content=true|limit=5'
  'q=SCRAMScriptFailure exit code 50513|search_content=true|limit=5'
)

for t in "${TESTS[@]}"; do
  echo "=== $t ==="
  IFS='|' read -ra parts <<< "$t"
  args=()
  for p in "${parts[@]}"; do
    args+=( --data-urlencode "$p" )
  done
  curl -sS --get "${args[@]}" 'http://node1616:7871/api/catalog/search' 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[])
print(f'hits: {len(h)}  duration_ms: {d.get(\"total_duration\")}')
for i,x in enumerate(h[:3]):
    print(f'  [{i+1}] {x.get(\"path\")}')
"
done

echo
echo "=== check if the embedder is running inside archi-services ==="
echo "  archi-services job:"
ARCHI_JID=$(squeue -u "$USER" -h -n archi-services -o "%i" 2>/dev/null | head -1)
echo "  jid=$ARCHI_JID"
if [ -n "$ARCHI_JID" ]; then
  echo "  log file:"
  ARCHI_LOG=$(ls -t ~/archi-services.*.out 2>/dev/null | head -1)
  echo "    $ARCHI_LOG"
  echo "  recent embedder / hybrid / vectorstore lines:"
  grep -iE "embed|vector|hybrid|pgvector" "$ARCHI_LOG" 2>/dev/null | tail -10
fi
REMOTE
