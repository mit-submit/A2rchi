#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== question_225 text ==="
python3 - <<'PY'
import json
qs = json.load(open("/home/mohoney/A2rchi/configs/submit75/curated_questions.json"))
q = next((q for q in qs if q.get("idx") == 225 or q.get("id") == 225), None)
if q:
    print(q.get("question", "")[:600])
PY
echo
echo "=== log entries mentioning question_225 ==="
LATEST_LOG=$(ls -t ~/archi-bench-v2.*.out 2>/dev/null | head -1)
grep "question_225" "$LATEST_LOG"
echo
echo "=== how long has the bench been alive? ==="
date -Iseconds
echo "job:"
squeue -u "$USER" -n archi-bench-v2 -o "%i %T %M %V"
REMOTE
