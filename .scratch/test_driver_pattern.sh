#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== curl with (?:test) — the pattern my probe sends ==="
curl -sS --get \
  --data-urlencode "q=(?:test)" \
  --data "limit=5" --data "mode=grep" --data "regex=true" --data "case_sensitive=false" --data "search_content=true" \
  --data "before=0" --data "after=0" --data "max_matches_per_file=3" \
  'http://node1616:7871/api/catalog/search' | python3 -c "
import json, sys
d = json.load(sys.stdin)
h = d.get('hits', [])
print(f'hits: {len(h)} dur={d.get(\"total_duration\")}')
for x in h[:3]: print('  ', x.get('path'))
"

echo
echo "=== curl with (?:T0_CH_CERN_Tape|disabled|production|output) ==="
curl -sS --get \
  --data-urlencode "q=(?:T0_CH_CERN_Tape|disabled|production|output)" \
  --data "limit=15" --data "mode=grep" --data "regex=true" --data "case_sensitive=false" --data "search_content=true" \
  --data "before=0" --data "after=0" --data "max_matches_per_file=3" \
  'http://node1616:7871/api/catalog/search' | python3 -c "
import json, sys
d = json.load(sys.stdin)
h = d.get('hits', [])
print(f'hits: {len(h)} dur={d.get(\"total_duration\")}')
for x in h[:3]: print('  ', x.get('path'))
"

echo
echo "=== same without (?:) — bare alternation ==="
curl -sS --get \
  --data-urlencode "q=T0_CH_CERN_Tape|disabled|production|output" \
  --data "limit=15" --data "mode=grep" --data "regex=true" --data "case_sensitive=false" --data "search_content=true" \
  'http://node1616:7871/api/catalog/search' | python3 -c "
import json, sys
d = json.load(sys.stdin)
h = d.get('hits', [])
print(f'hits: {len(h)} dur={d.get(\"total_duration\")}')
for x in h[:3]: print('  ', x.get('path'))
"
REMOTE
