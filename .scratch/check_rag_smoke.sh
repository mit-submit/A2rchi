#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M"
echo
echo "=== latest QA log ==="
LATEST_LOG=$(ls -t ~/archi-bench-qa.*.out 2>/dev/null | head -1)
echo "log: $LATEST_LOG"
if [ -f "$LATEST_LOG" ]; then
  cat "$LATEST_LOG"
fi
echo
echo "=== rag result file ==="
RES=~/bench_out/run_260q_orcd_v3/results_v3_rag.json
if [ -f "$RES" ]; then
  python3 - "$RES" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
r = d["benchmarking_results"][0]["single_question_results"]
print(f"questions: {len(r)}")
for qid, v in r.items():
    evs = v.get("trace_events") or []
    n_llm = sum(1 for e in evs if e.get("type") == "llm_call")
    n_ret = sum(1 for e in evs if e.get("type") == "rag_retrieve")
    ans = v.get("answer") or ""
    err = v.get("error") or ""
    sources = v.get("sources_metadata") or []
    print(f"  {qid}: time={v.get('time_elapsed', 0):.1f}s ans={len(ans)}ch llm_events={n_llm} retrieve_events={n_ret} hits={len(sources)} err={bool(err)}")
    if err:
        print(f"    ERROR: {err[:300]}")
        if v.get("traceback"):
            print(f"    TRACEBACK (first 600 ch):")
            print(v["traceback"][:600])
    elif ans:
        print(f"    answer preview: {ans[:200]!r}")
    for e in evs:
        if e.get("type") == "rag_retrieve":
            print(f"    rag_retrieve: n_hits={e.get('n_hits')} sources={e.get('doc_sources', [])[:3]}")
        elif e.get("type") == "llm_call":
            print(f"    llm_call: in_tok={e.get('input_tokens')} out_tok={e.get('output_tokens')} content_chars={e.get('content_chars')}")
PY
else
  echo "(no result file yet)"
fi
REMOTE
