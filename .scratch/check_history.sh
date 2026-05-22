#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== history samples ==="
wc -l ~/.bench-hw-history.jsonl
echo "=== timeline lines ==="
wc -l ~/.bench-question-timeline.jsonl
echo "=== latest history sample ==="
tail -1 ~/.bench-hw-history.jsonl
echo "=== first 3 timeline events ==="
head -3 ~/.bench-question-timeline.jsonl
echo "=== monitor log tail ==="
tail -5 ~/bench-monitor.log
REMOTE
