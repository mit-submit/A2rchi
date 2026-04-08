#!/usr/bin/env python3
"""
Real-time monitoring dashboard for A2rchi CHEP eval runs on submit75.
Polls submit75 via SSH for status, checkpoint progress, GPU utilization, and logs.

Usage:
    python scripts/eval_dashboard/app.py
    # Open http://localhost:5050
"""

import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, render_template

app = Flask(__name__, template_folder="templates", static_folder="static")

# ── Configuration ────────────────────────────────────────────────────────────
SSH_HOST = "mohoney@submit75.mit.edu"
SSH_OPTS = ["-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
REMOTE_ARCHI = "/home/submit/mohoney/archi"
TOTAL_QUESTIONS = 260
POLL_INTERVAL = 30  # seconds between SSH polls

# ── Shared state (updated by background thread) ─────────────────────────────
_state_lock = threading.Lock()
_state = {
    "last_poll": None,
    "ssh_ok": False,
    "status": {},           # eval_status.json contents
    "checkpoints": {},      # per-config question progress
    "gpu": [],              # GPU utilization
    "ollama_running": [],   # loaded models
    "log_tails": {},        # last N lines per config
    "process_alive": False, # is run_all_evals.sh running?
    "poll_error": None,
}


def _ssh(cmd: str, timeout: int = 20) -> tuple[bool, str]:
    """Run a command via SSH. Returns (success, stdout)."""
    try:
        result = subprocess.run(
            ["ssh"] + SSH_OPTS + [SSH_HOST, cmd],
            capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "SSH timeout"
    except Exception as e:
        return False, str(e)


def _poll():
    """Single polling cycle — gather all data from submit75."""
    data = {
        "last_poll": datetime.now(timezone.utc).isoformat(),
        "ssh_ok": False,
        "status": {},
        "checkpoints": {},
        "gpu": [],
        "ollama_running": [],
        "log_tails": {},
        "process_alive": False,
        "poll_error": None,
    }

    # 1. Mega SSH command to minimize round-trips
    mega_cmd = f"""
python3 << 'PY'
import json, subprocess, glob, os

REMOTE = "{REMOTE_ARCHI}"
TOTAL_Q = {TOTAL_QUESTIONS}
out = dict()

# eval_status.json
status_file = REMOTE + "/bench_out/eval_status.json"
try:
    with open(status_file) as f:
        out["status"] = json.load(f)
except Exception:
    out["status"] = dict()

# Checkpoint progress for each running/pending config
checkpoints = dict()
for cp_file in glob.glob(REMOTE + "/bench_out/benchmarking-eval-*.checkpoint.json"):
    try:
        with open(cp_file) as f:
            cp = json.load(f)
        name = os.path.basename(cp_file).replace("benchmarking-", "").replace(".checkpoint.json", "")
        ip = cp.get("in_progress", dict())
        checkpoints[name] = dict(
            question_id=ip.get("question_id", 0),
            total=TOTAL_Q,
            config_path=ip.get("config_path", ""),
            updated_at=cp.get("updated_at", ""),
            complete=cp.get("complete", False),
            completed_configs=len(cp.get("completed_configs", [])),
        )
    except Exception:
        pass
out["checkpoints"] = checkpoints

# GPU utilization
try:
    r = subprocess.run(
        ["nvidia-smi", "--query-gpu=index,name,memory.used,memory.total,utilization.gpu",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=5
    )
    gpus = []
    for line in r.stdout.strip().split("\\n"):
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 5:
            gpus.append(dict(
                index=int(parts[0]),
                name=parts[1],
                mem_used=int(parts[2]),
                mem_total=int(parts[3]),
                gpu_util=int(parts[4]),
            ))
    out["gpu"] = gpus
except Exception:
    out["gpu"] = []

# Ollama loaded models
try:
    import urllib.request
    resp = urllib.request.urlopen("http://localhost:11434/api/ps", timeout=5)
    ps = json.loads(resp.read())
    out["ollama_running"] = [
        dict(name=m.get("name",""), size_vram=m.get("size_vram",0))
        for m in ps.get("models", [])
    ]
except Exception:
    out["ollama_running"] = []

# Process alive?
try:
    r = subprocess.run(["pgrep", "-f", "run_all_evals"], capture_output=True, text=True, timeout=5)
    out["process_alive"] = bool(r.stdout.strip())
except Exception:
    out["process_alive"] = False

# Log tails (last 25 lines per config log)
logs = dict()
for log_file in glob.glob(REMOTE + "/bench_out/logs/*.log"):
    name = os.path.basename(log_file).replace(".log", "")
    try:
        with open(log_file) as f:
            lines = f.readlines()
            logs[name] = "".join(lines[-25:])
    except Exception:
        pass
out["log_tails"] = logs

# Output result files info
result_files = dict()
for rf in sorted(glob.glob(REMOTE + "/bench_out/benchmarking-eval-*.json")):
    if ".checkpoint." in rf:
        continue
    bname = os.path.basename(rf)
    sz = os.path.getsize(rf)
    result_files[bname] = dict(size=sz)
out["result_files"] = result_files

print(json.dumps(out))
PY
"""

    ok, stdout = _ssh(mega_cmd, timeout=30)
    if not ok:
        data["poll_error"] = stdout
        with _state_lock:
            _state.update(data)
        return

    data["ssh_ok"] = True
    try:
        parsed = json.loads(stdout)
        data["status"] = parsed.get("status", {})
        data["checkpoints"] = parsed.get("checkpoints", {})
        data["gpu"] = parsed.get("gpu", [])
        data["ollama_running"] = parsed.get("ollama_running", [])
        data["log_tails"] = parsed.get("log_tails", {})
        data["process_alive"] = parsed.get("process_alive", False)
        data["result_files"] = parsed.get("result_files", {})
    except json.JSONDecodeError as e:
        data["poll_error"] = f"JSON parse error: {e}\nRaw: {stdout[:500]}"

    with _state_lock:
        _state.update(data)


def _poll_loop():
    """Background thread that polls submit75 periodically."""
    while True:
        try:
            _poll()
        except Exception as e:
            with _state_lock:
                _state["poll_error"] = str(e)
        time.sleep(POLL_INTERVAL)


# ── Routes ───────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    with _state_lock:
        return jsonify(_state)


@app.route("/api/poll")
def api_poll():
    """Trigger an immediate poll (for manual refresh)."""
    threading.Thread(target=_poll, daemon=True).start()
    return jsonify({"ok": True, "message": "Poll triggered"})


@app.route("/api/log/<config_name>")
def api_log(config_name):
    """Get full log for a specific config (fetched live)."""
    ok, stdout = _ssh(
        f"tail -200 {REMOTE_ARCHI}/bench_out/logs/{config_name}.log 2>/dev/null || echo 'No log file'",
        timeout=15,
    )
    return jsonify({"ok": ok, "log": stdout})


# ── Main ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Starting eval dashboard → http://localhost:5050")
    print(f"Polling {SSH_HOST} every {POLL_INTERVAL}s")
    # Start background polling thread
    t = threading.Thread(target=_poll_loop, daemon=True)
    t.start()
    # Initial poll
    _poll()
    app.run(host="0.0.0.0", port=5050, debug=False)
