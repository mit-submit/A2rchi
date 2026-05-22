#!/bin/bash
ssh orcd-login bash <<'REMOTE'
echo "=== latest archi-bench-live log ==="
LIVE_LOG=$(ls -t ~/archi-bench-live.*.out 2>/dev/null | head -1)
echo "log: $LIVE_LOG"
if [ -n "$LIVE_LOG" ]; then
  echo "size: $(stat -c %s "$LIVE_LOG") bytes  mtime: $(stat -c %y "$LIVE_LOG")"
  echo "--- full content ---"
  cat "$LIVE_LOG"
fi
echo
echo "=== no-tools log: did the os._exit(0) hang it? ==="
NOTOOLS_LOG=$(ls -t ~/archi-bench-no-tools.*.out 2>/dev/null | head -1)
echo "log: $NOTOOLS_LOG"
if [ -n "$NOTOOLS_LOG" ]; then
  echo "size: $(stat -c %s "$NOTOOLS_LOG") bytes"
  echo "--- last 20 lines ---"
  tail -20 "$NOTOOLS_LOG"
fi
REMOTE
