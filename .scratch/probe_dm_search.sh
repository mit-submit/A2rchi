#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== catalog schema (count of docs) ==="
curl -sS 'http://node1616:7871/api/catalog/schema' 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f'counts: {d.get(\"counts\", {})}')
print(f'sources known: {list(d.get(\"sources\", {}).keys())[:10]}')"

echo
echo "=== same query, no mode (default) ==="
curl -sS 'http://node1616:7871/api/catalog/search?q=SCRAMScriptFailure&limit=5' 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[])
print(f'hits: {len(h)}')
for i,x in enumerate(h[:5]):
    print(f'  [{i+1}] {x.get(\"path\")} score={x.get(\"score\")}')"

echo
echo "=== same query, mode=grep ==="
curl -sS 'http://node1616:7871/api/catalog/search?q=SCRAMScriptFailure&limit=5&mode=grep' 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[])
print(f'hits: {len(h)}')
for i,x in enumerate(h[:5]):
    print(f'  [{i+1}] {x.get(\"path\")} score={x.get(\"score\")}')"

echo
echo "=== test query in hybrid mode ==="
curl -sS 'http://node1616:7871/api/catalog/search?q=test&limit=5&mode=hybrid' 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[])
print(f'hits: {len(h)}')
for i,x in enumerate(h[:5]):
    print(f'  [{i+1}] {x.get(\"path\")} score={x.get(\"score\")}')"

echo
echo "=== T0_CH_CERN_Tape default mode ==="
curl -sS 'http://node1616:7871/api/catalog/search?q=T0_CH_CERN_Tape&limit=5' 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[])
print(f'hits: {len(h)}')
for i,x in enumerate(h[:5]):
    print(f'  [{i+1}] {x.get(\"path\")} score={x.get(\"score\")}')"

echo
echo "=== list modes data-manager exposes ==="
curl -sS 'http://node1616:7871/api/catalog/search?q=test&limit=1&mode=hybrid' 2>&1 | python3 -c "
import json,sys
d=json.load(sys.stdin)
print(f'top-level keys: {list(d.keys())}')
print(f'meta: {d.get(\"meta\", {})}')
print(f'first hit keys: {list(d.get(\"hits\", [{}])[0].keys()) if d.get(\"hits\") else \"(no hits)\"}')"
REMOTE
