#!/bin/bash
# Background monitor. Every 30s, captures:
#   * GPU snapshot via srun --overlap on vllm job
#   * CPU snapshot via srun --overlap on bench job
#   * vLLM throughput/queue stats from its log
#   * Number of completed questions
# Writes:
#   * ~/.bench-hw-snapshot.json   — single-shot latest (atomic)
#   * ~/.bench-hw-history.jsonl   — append-only time series (one line / sample)
#   * ~/.bench-question-timeline.jsonl — append-only line per newly-completed question
#
# Run as: nohup bash ~/monitor_run.sh < /dev/null > /tmp/monitor-stdout.log 2>&1 &
# Exits when no archi-bench job is in the queue.

set -u
SNAP="$HOME/.bench-hw-snapshot.json"
HIST="$HOME/.bench-hw-history.jsonl"
TIMELINE="$HOME/.bench-question-timeline.jsonl"
SEEN_QIDS_FILE="$HOME/.bench-seen-qids.txt"
LOG="$HOME/bench-monitor.log"

: > "$HIST"
: > "$TIMELINE"
: > "$SEEN_QIDS_FILE"
echo "=== monitor started at $(date -Iseconds) ===" > "$LOG"

while true; do
  ts=$(date "+%Y-%m-%d %H:%M:%S")
  ts_epoch=$(date +%s)

  BENCH_JID=$(squeue -u "$USER" -h -o "%i %j" 2>/dev/null | awk '$2 ~ /^archi-bench/ {print $1; exit}')
  VLLM_JID=$(squeue -u "$USER" -h -n archi-vllm -o "%i" 2>/dev/null | head -1)

  if [ -z "$BENCH_JID" ]; then
    echo "=== $ts no archi-bench job; exiting ===" >> "$LOG"
    break
  fi

  GPU_CSV=""
  if [ -n "$VLLM_JID" ]; then
    GPU_CSV=$(timeout 10 srun --jobid="$VLLM_JID" --overlap nvidia-smi \
      --query-gpu=index,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,temperature.gpu \
      --format=csv,noheader 2>/dev/null || true)
  fi
  CPU_INFO=$(timeout 10 srun --jobid="$BENCH_JID" --overlap cat /proc/loadavg 2>/dev/null || true)
  CPU_MEM=$(timeout 10 srun --jobid="$BENCH_JID" --overlap bash -c 'free -m | grep "^Mem"' 2>/dev/null || true)

  VLLM_LOG=$(ls -t ~/archi-vllm.*.out 2>/dev/null | head -1)
  VLLM_LINE=""
  if [ -n "$VLLM_LOG" ]; then
    VLLM_LINE=$(grep "loggers.py.*Avg prompt throughput" "$VLLM_LOG" | tail -1)
  fi

  RES_FILE=$(ls -t ~/bench_out/run_260q_orcd*/*.json 2>/dev/null | head -1)

  python3 - <<PY > /tmp/sample.json
import json, os
ts = "$ts"
ts_epoch = int("$ts_epoch")
gpu_csv = """$GPU_CSV"""
cpu_info = """$CPU_INFO"""
cpu_mem = """$CPU_MEM"""
vllm_line = """$VLLM_LINE"""
res_file = "$RES_FILE"
seen_file = "$SEEN_QIDS_FILE"
timeline_file = "$TIMELINE"

gpus = []
for line in gpu_csv.splitlines():
    parts = [p.strip() for p in line.split(",")]
    if len(parts) >= 7:
        try:
            gpus.append({
                "idx": int(parts[0]),
                "util_gpu": float(parts[1].replace("%","").strip()),
                "util_mem": float(parts[2].replace("%","").strip()),
                "mem_used_mb": float(parts[3].split()[0]),
                "mem_total_mb": float(parts[4].split()[0]),
                "power_w": float(parts[5].split()[0]),
                "temp_c": float(parts[6].split()[0]),
            })
        except Exception:
            pass

cpu = {}
if cpu_info.strip():
    p = cpu_info.split()
    if len(p) >= 3:
        try:
            cpu["load1"] = float(p[0]); cpu["load5"] = float(p[1]); cpu["load15"] = float(p[2])
        except Exception:
            pass
if cpu_mem.startswith("Mem:"):
    p = cpu_mem.split()
    if len(p) >= 3:
        try:
            cpu["mem_total_mb"] = float(p[1]); cpu["mem_used_mb"] = float(p[2])
        except Exception:
            pass

vllm = {}
if vllm_line:
    try:
        vllm["prefill"] = float(vllm_line.split("Avg prompt throughput:")[1].split("tokens/s")[0].strip())
        vllm["gen"]     = float(vllm_line.split("Avg generation throughput:")[1].split("tokens/s")[0].strip())
        vllm["running"] = int(vllm_line.split("Running:")[1].split("reqs")[0].strip())
        vllm["waiting"] = int(vllm_line.split("Waiting:")[1].split("reqs")[0].strip())
        vllm["kv"]      = float(vllm_line.split("GPU KV cache usage:")[1].split("%")[0].strip())
    except Exception:
        pass

n_done = 0
new_qids = []
if res_file and os.path.exists(res_file):
    try:
        with open(res_file) as fh:
            d = json.load(fh)
        r = d.get("benchmarking_results", [{}])[0].get("single_question_results", {})
        n_done = len(r)
        seen = set()
        if os.path.exists(seen_file):
            with open(seen_file) as fh:
                seen = {ln.strip() for ln in fh if ln.strip()}
        for qid, v in r.items():
            if qid in seen:
                continue
            t_elapsed = v.get("time_elapsed", 0)
            new_qids.append({
                "qid": qid,
                "completed_ts": ts_epoch,
                "completed_str": ts,
                "time_elapsed": t_elapsed,
                "start_ts": ts_epoch - int(t_elapsed),
                "tools": sum(1 for ev in v.get("trace_events", []) if ev.get("type") == "tool_call"),
                "err": bool(v.get("error")),
            })
            seen.add(qid)
        with open(seen_file, "w") as fh:
            for qid in seen:
                fh.write(qid + "\n")
        if new_qids:
            with open(timeline_file, "a") as fh:
                for q in new_qids:
                    fh.write(json.dumps(q) + "\n")
    except Exception:
        pass

sample = {"ts": ts, "ts_epoch": ts_epoch, "gpus": gpus, "cpu": cpu, "vllm": vllm,
          "n_done": n_done, "n_new_qids": len(new_qids)}
print(json.dumps(sample))
PY

  if [ -s /tmp/sample.json ]; then
    cp /tmp/sample.json "$SNAP.tmp" && mv -f "$SNAP.tmp" "$SNAP"
    cat /tmp/sample.json >> "$HIST"
    echo >> "$HIST"
  fi

  N_DONE=$(python3 -c "import json; print(json.load(open('/tmp/sample.json')).get('n_done', '?'))" 2>/dev/null)
  echo "$ts  n_done=$N_DONE  load=$(echo $CPU_INFO | awk '{print $1}')" >> "$LOG"

  sleep 30
done

echo "=== monitor exited at $(date -Iseconds) ===" >> "$LOG"
