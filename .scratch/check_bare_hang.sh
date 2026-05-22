#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== bare job state ==="
squeue -u "$USER" -j 14249512 -o "%i %j %T %M"
echo
echo "=== bare log full tail (last 30) ==="
tail -30 ~/archi-bench-bare.14249512.out
echo
echo "=== python procs on the bench node ==="
BENCH_JID=$(squeue -u "$USER" -h -n archi-bench-bare -o "%i" 2>/dev/null | head -1)
if [ -n "$BENCH_JID" ]; then
  timeout 8 srun --jobid="$BENCH_JID" --overlap bash -c '
    ps -u mohoney -o pid,etime,pcpu,pmem,cmd | grep -E "python|run_260q" | grep -v grep | head -5
    echo ---
    cat /proc/loadavg
  ' 2>/dev/null
fi
REMOTE
