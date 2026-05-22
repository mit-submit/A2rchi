#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== exact condensed q0 in hybrid ==="
Q='When was T0_CH_CERN_Tape disabled for production output?'
curl -sS --get --data-urlencode "q=$Q" --data "limit=5" --data "mode=hybrid" 'http://node1616:7871/api/catalog/search' 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[])
print(f'hits: {len(h)}  duration_ms: {d.get(\"total_duration\")}')
for i,x in enumerate(h[:5]):
    print(f'  [{i+1}] {x.get(\"path\")}')
"

echo
echo "=== exact condensed q1 in hybrid ==="
Q='How should I approach resolving the SCRAMScriptFailure (Exit code: 50513) error in production workflows'
curl -sS --get --data-urlencode "q=$Q" --data "limit=5" --data "mode=hybrid" 'http://node1616:7871/api/catalog/search' 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[])
print(f'hits: {len(h)}  duration_ms: {d.get(\"total_duration\")}')
for i,x in enumerate(h[:5]):
    print(f'  [{i+1}] {x.get(\"path\")}')
"

echo
echo "=== same q0 in default mode ==="
Q='When was T0_CH_CERN_Tape disabled for production output?'
curl -sS --get --data-urlencode "q=$Q" --data "limit=5" 'http://node1616:7871/api/catalog/search' 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[])
print(f'hits: {len(h)}')
for i,x in enumerate(h[:5]):
    print(f'  [{i+1}] {x.get(\"path\")}')
"
REMOTE
