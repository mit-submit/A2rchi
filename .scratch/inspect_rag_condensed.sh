#!/bin/bash
ssh orcd-login bash <<'REMOTE'
python3 - <<'PY'
import json
d = json.load(open('/home/mohoney/bench_out/run_260q_orcd_v3/results_v3_rag.json'))
r = d['benchmarking_results'][0]['single_question_results']
for qid, v in r.items():
    print(f"=== {qid} ===")
    print(f"original question: {v['question'][:120]!r}")
    for e in v.get('trace_events', []):
        if e.get('type') == 'rag_retrieve':
            print(f"  rag_retrieve n_hits={e.get('n_hits')}")
            print(f"  condensed_output (full): {e.get('condensed_output')!r}")
            print(f"  doc_sources: {e.get('doc_sources')}")
    print(f"  answer ({len(v.get('answer',''))} ch): {v.get('answer','')[:300]!r}")
    print()
PY
echo
echo "=== try a direct hybrid search to compare ==="
echo "--- query 1: 'T0_CH_CERN_Tape disabled production' ---"
curl -sS 'http://node1616:7871/api/catalog/search?q=T0_CH_CERN_Tape+disabled+production&limit=5&mode=hybrid' 2>&1 | python3 -c "import json,sys; d=json.load(sys.stdin); h=d.get('hits',[]); print(f'hits: {len(h)}'); [print(f'  [{i+1}] {x.get(\"path\")} score={x.get(\"score\")}') for i,x in enumerate(h[:5])]"

echo
echo "--- query 2: 'SCRAMScriptFailure 50513' ---"
curl -sS 'http://node1616:7871/api/catalog/search?q=SCRAMScriptFailure+50513&limit=5&mode=hybrid' 2>&1 | python3 -c "import json,sys; d=json.load(sys.stdin); h=d.get('hits',[]); print(f'hits: {len(h)}'); [print(f'  [{i+1}] {x.get(\"path\")} score={x.get(\"score\")}') for i,x in enumerate(h[:5])]"
REMOTE
