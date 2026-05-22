#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== queue ==="
squeue -u "$USER" -o "%i %j %T %M"
echo
echo "=== no-tools count now ==="
python3 -c "
import json
d = json.load(open('/home/mohoney/bench_out/run_260q_orcd_v3/results_v3_no-tools.json'))
r = d['benchmarking_results'][0]['single_question_results']
errs = sum(1 for v in r.values() if v.get('error'))
print(f'{len(r)}/260  errors={errs}')
"
echo
echo "=== last 10 log lines ==="
LOG=$(ls -t ~/archi-bench-no-tools.*.out 2>/dev/null | head -1)
tail -10 "$LOG"
echo
echo "=== vllm last 3 throughput samples ==="
VLLM_LOG=$(ls -t ~/archi-vllm.14257646.out 2>/dev/null)
grep "Avg prompt throughput" "$VLLM_LOG" 2>/dev/null | tail -3
echo
echo "=== sample non-error answer to see what's working ==="
python3 - <<'PY'
import json
d = json.load(open('/home/mohoney/bench_out/run_260q_orcd_v3/results_v3_no-tools.json'))
r = d['benchmarking_results'][0]['single_question_results']
for qid, v in r.items():
    if not v.get('error'):
        print(f"{qid}: time={v.get('time_elapsed', 0):.1f}s tools={v.get('n_tool_calls')}")
        print(f"  answer (first 200): {(v.get('answer') or '')[:200]!r}")
        print()
        break

# Also one error
for qid, v in r.items():
    if v.get('error'):
        evs = v.get('trace_events') or []
        n_llm = sum(1 for e in evs if e.get('type') == 'llm_call')
        n_tool = sum(1 for e in evs if e.get('type') == 'tool_call')
        print(f"ERROR {qid}: time={v.get('time_elapsed', 0):.1f}s tools={n_tool} llm={n_llm}")
        # Recent events
        for e in evs[-4:]:
            print(f"  {e.get('type')}: {e.get('tool_name') or e.get('duration_s', 'n/a')}")
        break
PY
REMOTE
