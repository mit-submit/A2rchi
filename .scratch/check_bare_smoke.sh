#!/bin/bash
JOBID=${1:-14246792}
ssh orcd-login bash <<REMOTE
echo "=== queue ==="
squeue -u "\$USER" -o "%i %j %T %M"
echo
echo "=== log: ~/archi-bench-qa.$JOBID.out ==="
if [ -f ~/archi-bench-qa.$JOBID.out ]; then
  cat ~/archi-bench-qa.$JOBID.out
else
  echo "(no log yet)"
fi
echo
echo "=== result file ==="
RES=~/bench_out/run_260q_orcd_v3/results_v3_bare.json
if [ -f "\$RES" ]; then
  ls -la "\$RES"
  python3 - "\$RES" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
r = d["benchmarking_results"][0]["single_question_results"]
print(f"questions: {len(r)}")
for qid, v in r.items():
    evs = v.get("trace_events") or []
    n_llm = sum(1 for e in evs if e.get("type") == "llm_call")
    ans = v.get("answer") or ""
    err = v.get("error") or ""
    print(f"  {qid}: time={v.get('time_elapsed', 0):.1f}s ans={len(ans)}ch llm_events={n_llm} err={bool(err)}")
    if err:
        print(f"    ERROR: {err[:300]}")
    elif ans:
        print(f"    answer preview: {ans[:150]!r}")
    for e in evs:
        if e.get("type") == "llm_call":
            print(f"    llm_call: in_tok={e.get('input_tokens')} out_tok={e.get('output_tokens')} content_chars={e.get('content_chars')}")
PY
fi
REMOTE
