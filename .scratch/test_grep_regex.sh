#!/bin/bash
ssh orcd-login bash <<'REMOTE'
BASE='http://node1616:7871/api/catalog/search'

# 1: simple keyword via grep
echo "=== 1: grep, no regex, q='SCRAMScriptFailure' ==="
curl -sS --get \
  --data-urlencode "q=SCRAMScriptFailure" \
  --data "limit=5" --data "mode=grep" --data "search_content=true" \
  "$BASE" | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[]); print(f'hits: {len(h)} dur={d.get(\"total_duration\")}')
for x in h[:3]: print('  ', x.get('path'))"

# 2: same as 1 but regex=true
echo
echo "=== 2: grep, regex=true, q='SCRAMScriptFailure' ==="
curl -sS --get \
  --data-urlencode "q=SCRAMScriptFailure" \
  --data "limit=5" --data "mode=grep" --data "regex=true" --data "search_content=true" \
  "$BASE" | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[]); print(f'hits: {len(h)} dur={d.get(\"total_duration\")}')
for x in h[:3]: print('  ', x.get('path'))"

# 3: regex alternation
echo
echo "=== 3: grep, regex=true, q='(SCRAMScriptFailure|exit code|50513)' ==="
curl -sS --get \
  --data-urlencode "q=(SCRAMScriptFailure|exit code|50513)" \
  --data "limit=5" --data "mode=grep" --data "regex=true" --data "search_content=true" \
  "$BASE" | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[]); print(f'hits: {len(h)} dur={d.get(\"total_duration\")}')
for x in h[:3]: print('  ', x.get('path'))"

# 4: with \b word boundary (what driver sends)
echo
echo "=== 4: grep, regex=true, q='\\b(SCRAMScriptFailure|50513)\\b' ==="
curl -sS --get \
  --data-urlencode 'q=\b(SCRAMScriptFailure|50513)\b' \
  --data "limit=5" --data "mode=grep" --data "regex=true" --data "search_content=true" \
  "$BASE" | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[]); print(f'hits: {len(h)} dur={d.get(\"total_duration\")}')
for x in h[:3]: print('  ', x.get('path'))"

# 5: same with \b but case insensitive
echo
echo "=== 5: grep, regex=true, case_sensitive=false, q='\\b(SCRAMScriptFailure)\\b' ==="
curl -sS --get \
  --data-urlencode 'q=\b(SCRAMScriptFailure)\b' \
  --data "limit=5" --data "mode=grep" --data "regex=true" --data "case_sensitive=false" --data "search_content=true" \
  "$BASE" | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[]); print(f'hits: {len(h)} dur={d.get(\"total_duration\")}')
for x in h[:3]: print('  ', x.get('path'))"

# 6: non-capturing
echo
echo "=== 6: grep, regex=true, q='\\b(?:SCRAMScriptFailure)\\b' ==="
curl -sS --get \
  --data-urlencode 'q=\b(?:SCRAMScriptFailure)\b' \
  --data "limit=5" --data "mode=grep" --data "regex=true" \
  "$BASE" | python3 -c "
import json,sys
d=json.load(sys.stdin)
h=d.get('hits',[]); print(f'hits: {len(h)} dur={d.get(\"total_duration\")}')
for x in h[:3]: print('  ', x.get('path'))"
REMOTE
