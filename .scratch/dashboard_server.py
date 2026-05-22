"""Live HTML dashboard for the ORCD 260q bench run.

Usage:
    python3 .scratch/dashboard_server.py            # serve on localhost:8765
    python3 .scratch/dashboard_server.py 9000       # serve on a different port

Then open http://localhost:8765 in your browser. Page auto-refreshes every 5s.
Each refresh ssh's to orcd-login and pulls fresh state. The Python server is
the SSH client; the browser only sees a static-looking page.

Data pipeline:
    monitor_run.sh (on orcd-login)
        writes:
            ~/.bench-hw-snapshot.json         single-shot latest (atomic mv)
            ~/.bench-hw-history.jsonl         append-only 30s samples
            ~/.bench-question-timeline.jsonl  append-only line per completed q
    dashboard_server.py (this file, on laptop)
        ssh-fetches all three + the live bench results JSON, then renders.
"""

from __future__ import annotations

import json
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime
from html import escape

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765


REMOTE_SNAPSHOT_SH = r"""
set -u
queue=$(squeue -u "$USER" -o "%i|%j|%T|%M|%N|%l" 2>/dev/null)
bench_line=$(echo "$queue" | awk -F'|' '$2=="archi-bench"' | head -1)
vllm_line=$(echo "$queue" | awk -F'|' '$2=="archi-vllm"' | head -1)
arch_line=$(echo "$queue" | awk -F'|' '$2=="archi-services" || $2=="archi-se"' | head -1)

LATEST_RES=$(ls -t ~/bench_out/run_260q_orcd*/*.json 2>/dev/null | head -1)

SNAP=$(cat ~/.bench-hw-snapshot.json 2>/dev/null || echo '')
# Trim history to last ~2h (2h * 60min / 0.5min = 240 samples) to bound payload.
HIST_TAIL=$(tail -240 ~/.bench-hw-history.jsonl 2>/dev/null || echo '')
TIMELINE=$(cat ~/.bench-question-timeline.jsonl 2>/dev/null || echo '')

python3 - "$bench_line" "$vllm_line" "$arch_line" "$LATEST_RES" "$SNAP" "$HIST_TAIL" "$TIMELINE" <<'PY'
import json, sys, os

bench_line, vllm_line, arch_line, latest_res, snap_raw, hist_raw, timeline_raw = sys.argv[1:8]

def parse_q(line):
    if not line:
        return None
    p = line.split("|")
    if len(p) < 6:
        return None
    return {"jobid": p[0], "name": p[1], "state": p[2], "time": p[3], "node": p[4], "walltime": p[5]}

out = {
    "bench":  parse_q(bench_line),
    "vllm":   parse_q(vllm_line),
    "archi":  parse_q(arch_line),
    "results": None,
    "gpus_now": [],
    "cpu_now": {},
    "vllm_now": {},
    "hw_history": [],
    "question_timeline": [],
    "snapshot_ts": "",
}

if snap_raw.strip():
    try:
        snap = json.loads(snap_raw)
        out["gpus_now"] = snap.get("gpus", [])
        out["cpu_now"]  = snap.get("cpu", {})
        out["vllm_now"] = snap.get("vllm", {})
        out["snapshot_ts"] = snap.get("ts", "")
    except Exception as e:
        out["snapshot_err"] = f"{type(e).__name__}: {e}"

if hist_raw.strip():
    for line in hist_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out["hw_history"].append(json.loads(line))
        except Exception:
            continue

if timeline_raw.strip():
    for line in timeline_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out["question_timeline"].append(json.loads(line))
        except Exception:
            continue

if latest_res and os.path.exists(latest_res):
    try:
        import re
        from collections import defaultdict, Counter
        FAIL_PATTERNS = [
            ("explicit_error", re.compile(r"^\s*(Error|ToolException|Traceback|Exception)[\s:]", re.IGNORECASE)),
            ("denied",         re.compile(r"\b(denied|forbidden|unauthorized)\b", re.IGNORECASE)),
            ("http_error",     re.compile(r"\b(HTTP\s+)?(40[0-4]|50[0-3])\b")),
            ("empty_result",   re.compile(r"Total matching documents:\s*0|No matching|No results found|0 hits")),
            ("schema_error",   re.compile(r"unknown field|invalid query|parse_exception|illegal_argument", re.IGNORECASE)),
            ("timeout",        re.compile(r"timeout|timed\s+out", re.IGNORECASE)),
        ]
        def classify(out_prev):
            for label, pat in FAIL_PATTERNS:
                if pat.search(out_prev or ""):
                    return label
            return None

        d = json.load(open(latest_res))
        r = d.get("benchmarking_results", [{}])[0].get("single_question_results", {})
        items = list(r.items())
        done = len(items)
        errs = sum(1 for _, v in items if v.get("error"))
        times = [v.get("time_elapsed", 0) for _, v in items]
        ans_lens = [len((v.get("answer") or "")) for _, v in items]
        tool_counts = [sum(1 for ev in v.get("trace_events", []) if ev.get("type") == "tool_call") for _, v in items]

        # ---- tool-call analysis ----
        tool_call_total = Counter()
        tool_fail_by_cat = defaultdict(Counter)
        retry_after_ok = 0
        retry_after_fail = 0
        total_retries = 0
        dup_invocations = 0
        total_invocations = 0
        for v in r.values():
            prev_tool = None
            prev_fail = False
            seen_args = set()
            for ev in v.get("trace_events", []):
                t = ev.get("type")
                if t == "tool_call":
                    name = ev.get("tool_name", "?")
                    tool_call_total[name] += 1
                    total_invocations += 1
                    args_key = (name, json.dumps(ev.get("args", {}), sort_keys=True, default=str))
                    if args_key in seen_args:
                        dup_invocations += 1
                    seen_args.add(args_key)
                    if name == prev_tool:
                        total_retries += 1
                        if prev_fail:
                            retry_after_fail += 1
                        else:
                            retry_after_ok += 1
                    prev_tool = name
                elif t == "tool_output":
                    name = ev.get("tool_name", "?")
                    cat = classify(ev.get("output_preview", ""))
                    if cat:
                        tool_fail_by_cat[name][cat] += 1
                    prev_fail = bool(cat)

        # worst questions by tool count
        worst_qs = sorted(
            ((qid, sum(1 for e in v.get("trace_events", []) if e.get("type") == "tool_call"),
              v.get("time_elapsed", 0), bool(v.get("error")), (v.get("question") or "")[:120])
             for qid, v in r.items()),
            key=lambda t: -t[1],
        )[:10]
        worst_qs_list = [{"qid": qid, "tools": n, "time": t, "err": e, "question": q} for qid, n, t, e, q in worst_qs]

        # error message catalog
        err_catalog = Counter()
        err_examples = {}
        for qid, v in r.items():
            err = v.get("error") or ""
            if not err:
                continue
            # key by the first 80 chars or up to first ":" group
            key = err.split(":")[0][:60].strip() or err[:60]
            err_catalog[key] += 1
            if key not in err_examples:
                err_examples[key] = err[:240]

        # tool count vs time scatter (each question one point)
        scatter = []
        for qid, v in r.items():
            n_t = sum(1 for e in v.get("trace_events", []) if e.get("type") == "tool_call")
            scatter.append({
                "qid": qid,
                "tools": n_t,
                "time": v.get("time_elapsed", 0),
                "err": bool(v.get("error")),
            })

        last20 = []
        for qid, v in items[-20:]:
            last20.append({
                "qid": qid,
                "time": v.get("time_elapsed", 0),
                "tools": sum(1 for ev in v.get("trace_events", []) if ev.get("type") == "tool_call"),
                "ans_len": len((v.get("answer") or "")),
                "err": (v.get("error") or "")[:120],
                "question": (v.get("question") or "")[:160],
            })

        # tool failure summary in display-ready form
        tool_summary = []
        for name, total in tool_call_total.most_common():
            cats = tool_fail_by_cat.get(name, {})
            any_fail = sum(cats.values())
            tool_summary.append({
                "name": name,
                "total": total,
                "any_fail": any_fail,
                "fail_pct": 100.0 * any_fail / max(total, 1),
                "by_cat": dict(cats),
            })

        out["results"] = {
            "file": latest_res,
            "done": done,
            "total": 260,
            "errors": errs,
            "avg_time": (sum(times) / len(times)) if times else 0,
            "max_time": max(times) if times else 0,
            "min_time": min(times) if times else 0,
            "times": times,
            "ans_lens": ans_lens,
            "tool_counts": tool_counts,
            "last20": last20,
            "tool_summary": tool_summary,
            "tool_call_total": int(sum(tool_call_total.values())),
            "total_retries": total_retries,
            "retry_after_ok": retry_after_ok,
            "retry_after_fail": retry_after_fail,
            "dup_invocations": dup_invocations,
            "total_invocations": total_invocations,
            "worst_qs": worst_qs_list,
            "err_catalog": [{"key": k, "count": c, "example": err_examples.get(k, "")} for k, c in err_catalog.most_common()],
            "scatter": scatter,
        }
    except Exception as e:
        import traceback
        out["results"] = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1000:]}

print(json.dumps(out))
PY
"""


def fetch_state() -> dict:
    """SSH into ORCD-login and run the snapshot pipeline. Returns a dict."""
    try:
        proc = subprocess.run(
            ["ssh", "orcd-login", "bash", "-s"],
            input=REMOTE_SNAPSHOT_SH.encode(),
            capture_output=True,
            timeout=20,
        )
        if proc.returncode != 0:
            return {"error": f"ssh exit {proc.returncode}: {proc.stderr.decode()[:500]}"}
        return json.loads(proc.stdout.decode())
    except subprocess.TimeoutExpired:
        return {"error": "ssh timeout (orcd-login slow or down)"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def render_html(state: dict) -> str:
    if state.get("error"):
        return f"<html><body><pre>ERROR: {escape(state['error'])}</pre></body></html>"

    bench = state.get("bench") or {}
    vllm  = state.get("vllm") or {}
    archi = state.get("archi") or {}
    r     = state.get("results") or {}
    history  = state.get("hw_history") or []
    timeline = state.get("question_timeline") or []

    done   = r.get("done", 0)
    total  = r.get("total", 260)
    pct    = (100 * done / total) if total else 0
    errors = r.get("errors", 0)
    avg    = r.get("avg_time", 0)
    par    = 16
    remaining = total - done
    eta_s = (avg * remaining / par) if (avg and remaining) else 0
    eta_m = eta_s / 60

    last20 = r.get("last20", [])
    times  = r.get("times", [])

    # --- time-series from history.jsonl ---
    # Each sample: {ts, ts_epoch, gpus:[{idx,util_gpu,util_mem,mem_used_mb,mem_total_mb,power_w,temp_c}],
    #               cpu:{load1,load5,load15,mem_used_mb,mem_total_mb}, vllm:{prefill,gen,running,waiting,kv}, n_done}
    series_ts = [s.get("ts", "") for s in history]
    # GPUs: detect indices present
    gpu_indices = set()
    for s in history:
        for g in s.get("gpus", []):
            gpu_indices.add(g.get("idx"))
    gpu_indices = sorted(gpu_indices)

    def gpu_series(field: str) -> dict:
        out = {idx: [] for idx in gpu_indices}
        for s in history:
            by_idx = {g.get("idx"): g for g in s.get("gpus", [])}
            for idx in gpu_indices:
                g = by_idx.get(idx)
                out[idx].append(g.get(field) if g else None)
        return out

    gpu_util  = gpu_series("util_gpu")
    gpu_power = gpu_series("power_w")
    gpu_mem   = {idx: [(v / 1024.0) if v is not None else None for v in lst] for idx, lst in gpu_series("mem_used_mb").items()}
    gpu_temp  = gpu_series("temp_c")

    cpu_load1  = [s.get("cpu", {}).get("load1")  for s in history]
    cpu_load5  = [s.get("cpu", {}).get("load5")  for s in history]
    cpu_load15 = [s.get("cpu", {}).get("load15") for s in history]
    cpu_mem    = [((s.get("cpu", {}).get("mem_used_mb") or 0) / 1024.0) for s in history]

    vllm_prefill = [s.get("vllm", {}).get("prefill") for s in history]
    vllm_gen     = [s.get("vllm", {}).get("gen")     for s in history]
    vllm_running = [s.get("vllm", {}).get("running") for s in history]
    vllm_waiting = [s.get("vllm", {}).get("waiting") for s in history]
    vllm_kv      = [s.get("vllm", {}).get("kv")      for s in history]
    n_done_series = [s.get("n_done") for s in history]

    # Question event shapes (Plotly). Convert ts_epoch → "ts" string per sample.
    # We'll pass an array of {start: "YYYY-MM-DD HH:MM:SS", end: "...", qid: "..."} to JS.
    # Reverse epoch → string using the same %Y-%m-%d %H:%M:%S format the monitor uses.
    import time as _t
    def fmt(epoch):
        try:
            return _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(int(epoch)))
        except Exception:
            return ""
    q_events = [
        {
            "qid": q.get("qid", "")[:40],
            "start": fmt(q.get("start_ts", 0)),
            "end":   fmt(q.get("completed_ts", 0)),
            "err": bool(q.get("err")),
        }
        for q in timeline
    ]

    # --- current-state cards ---
    gpus_now = state.get("gpus_now") or []
    cpu_now  = state.get("cpu_now") or {}
    vllm_now = state.get("vllm_now") or {}
    snap_ts  = state.get("snapshot_ts") or ""

    def job_card(label: str, j: dict) -> str:
        if not j:
            return f"<div class='card'><div class='lbl'>{label}</div><div class='val muted'>not in queue</div></div>"
        return (
            f"<div class='card'>"
            f"<div class='lbl'>{label}</div>"
            f"<div class='val'>{escape(j['name'])} · "
            f"<span class='state {escape(j['state'].lower())}'>{escape(j['state'])}</span></div>"
            f"<div class='sub'>job <code>{escape(j['jobid'])}</code> · "
            f"node <code>{escape(j['node'])}</code> · "
            f"{escape(j['time'])} elapsed / {escape(j['walltime'])}</div>"
            f"</div>"
        )

    def gpu_card(g: dict) -> str:
        util = g["util_gpu"]
        mem_pct = 100 * g["mem_used_mb"] / max(g["mem_total_mb"], 1)
        util_color = "#6dd58c" if util > 60 else ("#e3b341" if util > 20 else "#ff7373")
        return (
            f"<div class='card'>"
            f"<div class='lbl'>GPU {g['idx']}</div>"
            f"<div class='val' style='color:{util_color}'>{util:.0f}% util · {g['power_w']:.0f}W</div>"
            f"<div class='sub'>mem {g['mem_used_mb']/1024:.0f}/{g['mem_total_mb']/1024:.0f} GB ({mem_pct:.0f}%) · {g['temp_c']:.0f}°C · mem-bus {g['util_mem']:.0f}%</div>"
            f"</div>"
        )

    if gpus_now:
        gpu_cards_html = "".join(gpu_card(g) for g in gpus_now)
    else:
        gpu_cards_html = "<div class='card'><div class='lbl'>GPU</div><div class='val muted'>no live snapshot</div></div>"

    if cpu_now:
        cpu_card_html = (
            f"<div class='card'>"
            f"<div class='lbl'>CPU (bench node)</div>"
            f"<div class='val'>load {cpu_now.get('load1', 0):.2f} / {cpu_now.get('load5', 0):.2f} / {cpu_now.get('load15', 0):.2f}</div>"
            f"<div class='sub'>mem "
            f"{cpu_now.get('mem_used_mb', 0)/1024:.1f}/{cpu_now.get('mem_total_mb', 0)/1024:.1f} GB used"
            f"</div></div>"
        )
    else:
        cpu_card_html = "<div class='card'><div class='lbl'>CPU</div><div class='val muted'>no live snapshot</div></div>"

    if vllm_now:
        vllm_card_html = (
            f"<div class='card'>"
            f"<div class='lbl'>vLLM (latest sample)</div>"
            f"<div class='val'>prefill <b>{vllm_now.get('prefill', 0):.0f}</b> · gen <b>{vllm_now.get('gen', 0):.0f}</b> tok/s</div>"
            f"<div class='sub'>running {vllm_now.get('running', 0)} · waiting {vllm_now.get('waiting', 0)} · KV {vllm_now.get('kv', 0):.1f}%"
            f" · snapshot {escape(snap_ts)}"
            f"</div></div>"
        )
    else:
        vllm_card_html = "<div class='card'><div class='lbl'>vLLM</div><div class='val muted'>no live snapshot</div></div>"

    rows = []
    for q in reversed(last20):
        err = q["err"]
        cls = "err" if err else ""
        rows.append(
            f"<tr class='{cls}'>"
            f"<td><code>{escape(q['qid'])}</code></td>"
            f"<td class='num'>{q['time']:.1f}s</td>"
            f"<td class='num'>{q['tools']}</td>"
            f"<td class='num'>{q['ans_len']}</td>"
            f"<td>{escape(q['question'])}</td>"
            f"<td class='err'>{escape(err)}</td>"
            f"</tr>"
        )
    table_rows = "\n".join(rows) if rows else "<tr><td colspan=6 class='muted'>(no results yet)</td></tr>"

    # --- workload analytics ---
    tool_summary = r.get("tool_summary", [])
    worst_qs     = r.get("worst_qs", [])
    err_catalog  = r.get("err_catalog", [])
    scatter      = r.get("scatter", [])
    total_invs   = r.get("total_invocations", 0)
    dup_invs     = r.get("dup_invocations", 0)
    total_retries = r.get("total_retries", 0)
    retry_after_fail = r.get("retry_after_fail", 0)
    retry_after_ok   = r.get("retry_after_ok", 0)

    # tool failure stacked bar data
    tool_names_for_bar = [t["name"] for t in tool_summary]
    tool_totals_bar    = [t["total"] for t in tool_summary]
    tool_ok_bar        = [t["total"] - t["any_fail"] for t in tool_summary]
    tool_fail_bar      = [t["any_fail"] for t in tool_summary]

    # tool failure-category stacked bar
    all_categories = ["empty_result", "explicit_error", "denied", "http_error", "schema_error", "timeout"]
    cat_bars = {cat: [t.get("by_cat", {}).get(cat, 0) for t in tool_summary] for cat in all_categories}

    # tools vs wall-time scatter
    sc_n   = [s["tools"] for s in scatter]
    sc_t   = [s["time"]  for s in scatter]
    sc_err = [s["err"]   for s in scatter]
    sc_qid = [s["qid"]   for s in scatter]

    workload_cards = (
        f"<div class='card'><div class='lbl'>Total tool calls</div>"
        f"<div class='val'><b>{r.get('tool_call_total', 0):,}</b></div>"
        f"<div class='sub'>avg <b>{(r.get('tool_call_total', 0)/max(done,1)):.1f}</b> calls/question</div></div>"
        f"<div class='card'><div class='lbl'>Duplicate invocations</div>"
        f"<div class='val'><b>{dup_invs:,}</b> · <b>{100*dup_invs/max(total_invs,1):.1f}%</b></div>"
        f"<div class='sub'>same (tool, args) repeated within one question</div></div>"
        f"<div class='card'><div class='lbl'>Same-tool retries</div>"
        f"<div class='val'><b>{total_retries:,}</b></div>"
        f"<div class='sub'>after fail <b>{retry_after_fail}</b> ({100*retry_after_fail/max(total_retries,1):.0f}%) · "
        f"after ok <b>{retry_after_ok}</b> ({100*retry_after_ok/max(total_retries,1):.0f}%)</div></div>"
        f"<div class='card'><div class='lbl'>Errored questions</div>"
        f"<div class='val'><b>{errors}</b> · <b>{100*errors/max(done,1):.1f}%</b></div>"
        f"<div class='sub'>{len(err_catalog)} distinct error classes</div></div>"
    )

    # worst-N table
    worst_rows = []
    for q in worst_qs:
        cls = "err" if q.get("err") else ""
        worst_rows.append(
            f"<tr class='{cls}'>"
            f"<td><code>{escape(q['qid'])}</code></td>"
            f"<td class='num'>{q['tools']}</td>"
            f"<td class='num'>{q['time']:.1f}s</td>"
            f"<td>{escape(q['question'])}</td>"
            f"</tr>"
        )
    worst_rows_html = "\n".join(worst_rows) if worst_rows else "<tr><td colspan=4 class='muted'>(no data)</td></tr>"

    # error catalog table
    err_rows = []
    for e in err_catalog:
        err_rows.append(
            f"<tr>"
            f"<td class='num'>{e['count']}</td>"
            f"<td><code>{escape(e['key'])}</code></td>"
            f"<td class='err' style='font-size:11px;'>{escape(e['example'])}</td>"
            f"</tr>"
        )
    err_rows_html = "\n".join(err_rows) if err_rows else "<tr><td colspan=3 class='muted'>(no errors)</td></tr>"

    # --- HTML ---
    return f"""<!doctype html><html><head>
<meta charset="utf-8">
<title>Archi 260Q Bench</title>
<meta http-equiv="refresh" content="5">
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
  :root {{
    --bg: #0c0e13;
    --panel: #161922;
    --border: #242938;
    --text: #d6dae5;
    --muted: #6b7488;
    --accent: #6ea8ff;
    --good: #6dd58c;
    --warn: #e3b341;
    --err:  #ff7373;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "SF Pro Text", "Segoe UI", Roboto, system-ui, sans-serif;
        background: var(--bg); color: var(--text); margin: 0; padding: 16px; }}
  h1 {{ margin: 0 0 14px 0; font-weight: 600; font-size: 22px; letter-spacing: 0.02em; }}
  h1 .ts {{ float: right; font-size: 12px; color: var(--muted); font-weight: 400; }}
  h3 {{ margin: 18px 0 8px 0; font-size: 13px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.07em; font-weight: 500; }}
  .row {{ display: grid; gap: 12px; }}
  .row.cards3 {{ grid-template-columns: repeat(3, 1fr); }}
  .row.cards4 {{ grid-template-columns: repeat(4, 1fr); }}
  .row.split  {{ grid-template-columns: 1fr 1fr; }}
  .row.thirds {{ grid-template-columns: repeat(3, 1fr); }}
  .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }}
  .lbl {{ font-size: 11px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }}
  .val {{ font-size: 16px; margin-top: 4px; font-variant-numeric: tabular-nums; }}
  .val b {{ color: var(--text); }}
  .sub {{ font-size: 12px; color: var(--muted); margin-top: 4px; font-variant-numeric: tabular-nums; }}
  .state {{ font-size: 12px; padding: 2px 6px; border-radius: 3px; }}
  .state.running {{ background: rgba(109,213,140,0.15); color: var(--good); }}
  .state.pending {{ background: rgba(227,179,65,0.15); color: var(--warn); }}
  .state.completing {{ background: rgba(110,168,255,0.15); color: var(--accent); }}
  .progress {{ height: 28px; background: var(--panel); border: 1px solid var(--border); border-radius: 4px; overflow: hidden; position: relative; margin: 14px 0 6px 0; }}
  .progress .bar {{ height: 100%; background: linear-gradient(90deg, #4a7cf7, #6dd58c); width: {pct:.1f}%; transition: width 0.5s ease; }}
  .progress .pct {{ position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; font-variant-numeric: tabular-nums; font-weight: 600; mix-blend-mode: difference; color: white; }}
  .summary {{ font-size: 13px; color: var(--muted); font-variant-numeric: tabular-nums; margin-bottom: 6px; }}
  .summary b {{ color: var(--text); font-weight: 600; }}
  .controls {{ display: flex; gap: 16px; align-items: center; margin: 6px 0 0 0; font-size: 12px; color: var(--muted); }}
  .controls label {{ user-select: none; cursor: pointer; }}
  .controls input[type=checkbox] {{ accent-color: var(--accent); margin-right: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }}
  th {{ text-align: left; color: var(--muted); font-weight: 500; padding: 6px 8px; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid var(--border); }}
  td {{ padding: 6px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  td.num {{ text-align: right; }}
  tr.err td {{ color: #ffa999; }}
  td.err {{ font-size: 12px; color: var(--err); }}
  code {{ font-size: 12px; color: var(--accent); }}
  .muted {{ color: var(--muted); }}
  .chart {{ background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 8px; }}
</style></head><body>

<h1>Archi 260Q Benchmark <span class="ts">{escape(datetime.now().strftime("%H:%M:%S"))} · snapshot {escape(snap_ts)}</span></h1>

<div class="row cards3">
  {job_card("BENCH",  bench)}
  {job_card("vLLM",   vllm)}
  {job_card("Archi services", archi)}
</div>

<div class="row cards4" style="margin-top:12px;">
  {gpu_cards_html}
  {cpu_card_html}
  {vllm_card_html}
</div>

<div class="progress">
  <div class="bar"></div>
  <div class="pct">{done} / {total} questions · {pct:.1f}%</div>
</div>
<div class="summary">
  <b>{done}</b> done · <b>{errors}</b> errors · avg <b>{avg:.1f}s</b>/q · max <b>{r.get('max_time', 0):.1f}s</b> · min <b>{r.get('min_time', 0):.1f}s</b> · ETA at N={par} parallel ≈ <b>{eta_m:.1f} min</b>
</div>
<div class="controls">
  <label><input type="checkbox" id="show_qstart" checked> show question starts</label>
  <label><input type="checkbox" id="show_qend"> show question ends</label>
  <span class="muted">({len(q_events)} questions in timeline)</span>
</div>

<h3>GPU — utilization &amp; power</h3>
<div class="row split">
  <div class="chart" id="chart_gpu_util"  style="height:260px;"></div>
  <div class="chart" id="chart_gpu_power" style="height:260px;"></div>
</div>

<h3>GPU — memory &amp; temperature</h3>
<div class="row split">
  <div class="chart" id="chart_gpu_mem"  style="height:240px;"></div>
  <div class="chart" id="chart_gpu_temp" style="height:240px;"></div>
</div>

<h3>CPU (bench node)</h3>
<div class="row split">
  <div class="chart" id="chart_cpu_load" style="height:220px;"></div>
  <div class="chart" id="chart_cpu_mem"  style="height:220px;"></div>
</div>

<h3>vLLM</h3>
<div class="row split">
  <div class="chart" id="chart_vllm_throughput" style="height:240px;"></div>
  <div class="chart" id="chart_vllm_batch"      style="height:240px;"></div>
</div>
<div class="row split" style="margin-top:12px;">
  <div class="chart" id="chart_vllm_kv"   style="height:220px;"></div>
  <div class="chart" id="chart_progress"  style="height:220px;"></div>
</div>

<h3>Workload — tool calls &amp; failures across {done} questions</h3>
<div class="row cards4">
  {workload_cards}
</div>

<div class="row split" style="margin-top:12px;">
  <div class="chart" id="chart_tool_count"  style="height:300px;"></div>
  <div class="chart" id="chart_tool_failrate" style="height:300px;"></div>
</div>

<div class="row split" style="margin-top:12px;">
  <div class="chart" id="chart_tools_vs_time" style="height:340px;"></div>
  <div class="chart" id="chart_qhist"          style="height:340px;"></div>
</div>

<h3>Worst questions by tool-call count</h3>
<table>
  <thead><tr><th>qid</th><th class="num">tool calls</th><th class="num">wall</th><th>question (truncated)</th></tr></thead>
  <tbody>{worst_rows_html}</tbody>
</table>

<h3>Error catalog</h3>
<table>
  <thead><tr><th class="num">count</th><th>class</th><th>example</th></tr></thead>
  <tbody>{err_rows_html}</tbody>
</table>

<h3>Last 20 questions</h3>
<table>
  <thead><tr><th>qid</th><th class="num">time</th><th class="num">tools</th><th class="num">ans_len</th><th>question (truncated)</th><th>error</th></tr></thead>
  <tbody>{table_rows}</tbody>
</table>

<script>
const ts            = {json.dumps(series_ts)};
const gpu_indices   = {json.dumps(gpu_indices)};
const gpu_util      = {json.dumps(gpu_util)};
const gpu_power     = {json.dumps(gpu_power)};
const gpu_mem_gb    = {json.dumps(gpu_mem)};
const gpu_temp      = {json.dumps(gpu_temp)};
const cpu_load1     = {json.dumps(cpu_load1)};
const cpu_load5     = {json.dumps(cpu_load5)};
const cpu_load15    = {json.dumps(cpu_load15)};
const cpu_mem_gb    = {json.dumps(cpu_mem)};
const vllm_prefill  = {json.dumps(vllm_prefill)};
const vllm_gen      = {json.dumps(vllm_gen)};
const vllm_running  = {json.dumps(vllm_running)};
const vllm_waiting  = {json.dumps(vllm_waiting)};
const vllm_kv       = {json.dumps(vllm_kv)};
const n_done_series = {json.dumps(n_done_series)};
const q_times       = {json.dumps(times)};
const q_events      = {json.dumps(q_events)};

const GPU_COLORS = ['#6dd58c', '#6ea8ff', '#e3b341', '#ff7373', '#c084fc', '#fb923c'];

const base = {{
  paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
  font: {{color: '#9aa2b6', size: 11, family: 'system-ui'}},
  margin: {{l: 50, r: 14, t: 30, b: 38}},
  xaxis: {{gridcolor: '#242938', linecolor: '#242938'}},
  yaxis: {{gridcolor: '#242938', linecolor: '#242938'}},
  showlegend: true,
  legend: {{orientation: 'h', y: 1.18, font: {{size: 10}} }},
}};

function mkLayout(title, yTitle, extra) {{
  return Object.assign({{}}, base, {{
    title: {{text: title, font: {{size: 12, color: '#d6dae5'}} }},
    yaxis: Object.assign({{}}, base.yaxis, {{title: {{text: yTitle, font: {{size: 10}} }} }}),
  }}, extra || {{}});
}}

function gpuTraces(seriesByIdx, name) {{
  return gpu_indices.map((idx, i) => ({{
    x: ts, y: seriesByIdx[idx],
    name: 'GPU ' + idx + ' ' + name,
    type: 'scatter', mode: 'lines',
    line: {{color: GPU_COLORS[i % GPU_COLORS.length], width: 1.6}},
    connectgaps: false,
  }}));
}}

function buildShapes() {{
  const showStart = document.getElementById('show_qstart').checked;
  const showEnd   = document.getElementById('show_qend').checked;
  if (!showStart && !showEnd) return [];
  const shapes = [];
  for (const q of q_events) {{
    if (showStart && q.start) {{
      shapes.push({{
        type: 'line', xref: 'x', yref: 'paper',
        x0: q.start, x1: q.start, y0: 0, y1: 1,
        line: {{color: q.err ? 'rgba(255,115,115,0.25)' : 'rgba(109,213,140,0.18)', width: 1, dash: 'dot'}},
      }});
    }}
    if (showEnd && q.end) {{
      shapes.push({{
        type: 'line', xref: 'x', yref: 'paper',
        x0: q.end, x1: q.end, y0: 0, y1: 1,
        line: {{color: q.err ? 'rgba(255,115,115,0.40)' : 'rgba(110,168,255,0.30)', width: 1, dash: 'solid'}},
      }});
    }}
  }}
  return shapes;
}}

const CHARTS_WITH_OVERLAY = [
  'chart_gpu_util', 'chart_gpu_power', 'chart_gpu_mem', 'chart_gpu_temp',
  'chart_cpu_load', 'chart_cpu_mem',
  'chart_vllm_throughput', 'chart_vllm_batch', 'chart_vllm_kv', 'chart_progress',
];

function applyShapes() {{
  const shapes = buildShapes();
  CHARTS_WITH_OVERLAY.forEach(id => {{
    Plotly.relayout(id, {{shapes: shapes}});
  }});
}}

Plotly.newPlot('chart_gpu_util',
  gpuTraces(gpu_util, 'util %'),
  mkLayout('GPU utilization', '%', {{yaxis: Object.assign({{}}, base.yaxis, {{title: {{text:'%'}}, range:[0,100]}}) }}),
  {{displayModeBar:false, responsive:true}}
);
Plotly.newPlot('chart_gpu_power',
  gpuTraces(gpu_power, 'W'),
  mkLayout('GPU power draw', 'W'),
  {{displayModeBar:false, responsive:true}}
);
Plotly.newPlot('chart_gpu_mem',
  gpuTraces(gpu_mem_gb, 'GB used'),
  mkLayout('GPU memory (used, GB)', 'GB'),
  {{displayModeBar:false, responsive:true}}
);
Plotly.newPlot('chart_gpu_temp',
  gpuTraces(gpu_temp, '°C'),
  mkLayout('GPU temperature', '°C'),
  {{displayModeBar:false, responsive:true}}
);

Plotly.newPlot('chart_cpu_load',
  [
    {{x:ts, y:cpu_load1,  name:'load1',  type:'scatter', mode:'lines', line:{{color:'#6dd58c', width:1.6}} }},
    {{x:ts, y:cpu_load5,  name:'load5',  type:'scatter', mode:'lines', line:{{color:'#6ea8ff', width:1.4}} }},
    {{x:ts, y:cpu_load15, name:'load15', type:'scatter', mode:'lines', line:{{color:'#e3b341', width:1.2, dash:'dot'}} }},
  ],
  mkLayout('CPU load (1m/5m/15m)', 'load'),
  {{displayModeBar:false, responsive:true}}
);
Plotly.newPlot('chart_cpu_mem',
  [{{x:ts, y:cpu_mem_gb, name:'used GB', type:'scatter', mode:'lines', line:{{color:'#c084fc', width:1.6}}, fill:'tozeroy', fillcolor:'rgba(192,132,252,0.12)' }}],
  mkLayout('CPU memory used (GB)', 'GB'),
  {{displayModeBar:false, responsive:true}}
);

Plotly.newPlot('chart_vllm_throughput',
  [
    {{x:ts, y:vllm_prefill, name:'prefill tok/s', type:'scatter', mode:'lines', line:{{color:'#6dd58c', width:1.6}} }},
    {{x:ts, y:vllm_gen,     name:'gen tok/s',     type:'scatter', mode:'lines', yaxis:'y2', line:{{color:'#6ea8ff', width:1.6}} }},
  ],
  mkLayout('vLLM throughput', 'prefill tok/s', {{
    yaxis2: {{title:{{text:'gen tok/s', font:{{size:10}} }}, overlaying:'y', side:'right', showgrid:false, color:'#6ea8ff'}},
  }}),
  {{displayModeBar:false, responsive:true}}
);
Plotly.newPlot('chart_vllm_batch',
  [
    {{x:ts, y:vllm_running, name:'running', type:'scatter', mode:'lines', stackgroup:'one', line:{{color:'#6dd58c'}} }},
    {{x:ts, y:vllm_waiting, name:'waiting', type:'scatter', mode:'lines', stackgroup:'one', line:{{color:'#e3b341'}} }},
  ],
  mkLayout('vLLM in-flight requests', 'count'),
  {{displayModeBar:false, responsive:true}}
);
Plotly.newPlot('chart_vllm_kv',
  [{{x:ts, y:vllm_kv, name:'KV %', type:'scatter', mode:'lines', line:{{color:'#ff7373', width:1.6}}, fill:'tozeroy', fillcolor:'rgba(255,115,115,0.08)' }}],
  mkLayout('GPU KV cache usage', '%', {{yaxis: Object.assign({{}}, base.yaxis, {{title:{{text:'%'}}, rangemode:'tozero'}}) }}),
  {{displayModeBar:false, responsive:true}}
);
Plotly.newPlot('chart_progress',
  [{{x:ts, y:n_done_series, name:'questions done', type:'scatter', mode:'lines', line:{{color:'#6ea8ff', width:1.8}}, fill:'tozeroy', fillcolor:'rgba(110,168,255,0.10)' }}],
  mkLayout('Questions completed (cumulative)', 'count'),
  {{displayModeBar:false, responsive:true}}
);

Plotly.newPlot('chart_qhist',
  [{{x:q_times, type:'histogram', marker:{{color:'#6ea8ff'}}, nbinsx:30}}],
  Object.assign({{}}, base, {{
    title: {{text:'per-question wall time', font:{{size:12, color:'#d6dae5'}} }},
    xaxis: Object.assign({{}}, base.xaxis, {{title:{{text:'seconds', font:{{size:10}} }} }}),
    yaxis: Object.assign({{}}, base.yaxis, {{title:{{text:'count', font:{{size:10}} }} }}),
  }}),
  {{displayModeBar:false, responsive:true}}
);

// --- workload analytics ---
const tool_names    = {json.dumps(tool_names_for_bar)};
const tool_ok       = {json.dumps(tool_ok_bar)};
const tool_fail     = {json.dumps(tool_fail_bar)};
const tool_totals   = {json.dumps(tool_totals_bar)};
const cat_empty     = {json.dumps(cat_bars["empty_result"])};
const cat_explicit  = {json.dumps(cat_bars["explicit_error"])};
const cat_denied    = {json.dumps(cat_bars["denied"])};
const cat_http      = {json.dumps(cat_bars["http_error"])};
const cat_schema    = {json.dumps(cat_bars["schema_error"])};
const cat_timeout   = {json.dumps(cat_bars["timeout"])};
const sc_n   = {json.dumps(sc_n)};
const sc_t   = {json.dumps(sc_t)};
const sc_err = {json.dumps(sc_err)};
const sc_qid = {json.dumps(sc_qid)};

Plotly.newPlot('chart_tool_count',
  [
    {{x: tool_names, y: tool_ok,   name:'ok',   type:'bar', marker:{{color:'#6dd58c'}} }},
    {{x: tool_names, y: tool_fail, name:'fail', type:'bar', marker:{{color:'#ff7373'}} }},
  ],
  Object.assign({{}}, base, {{
    title: {{text:'Tool-call frequency (ok vs failed)', font:{{size:12, color:'#d6dae5'}} }},
    barmode: 'stack',
    yaxis: Object.assign({{}}, base.yaxis, {{title:{{text:'calls', font:{{size:10}} }} }}),
    xaxis: Object.assign({{}}, base.xaxis, {{tickangle: -30}}),
    margin: {{l:50, r:14, t:30, b:120}},
  }}),
  {{displayModeBar:false, responsive:true}}
);

Plotly.newPlot('chart_tool_failrate',
  [
    {{x: tool_names, y: cat_empty,    name:'empty_result',    type:'bar', marker:{{color:'#e3b341'}} }},
    {{x: tool_names, y: cat_explicit, name:'explicit_error',  type:'bar', marker:{{color:'#ff7373'}} }},
    {{x: tool_names, y: cat_denied,   name:'denied',          type:'bar', marker:{{color:'#c084fc'}} }},
    {{x: tool_names, y: cat_http,     name:'http_error',      type:'bar', marker:{{color:'#fb923c'}} }},
    {{x: tool_names, y: cat_schema,   name:'schema_error',    type:'bar', marker:{{color:'#6ea8ff'}} }},
    {{x: tool_names, y: cat_timeout,  name:'timeout',         type:'bar', marker:{{color:'#ec4899'}} }},
  ],
  Object.assign({{}}, base, {{
    title: {{text:'Failures by category, per tool', font:{{size:12, color:'#d6dae5'}} }},
    barmode: 'stack',
    yaxis: Object.assign({{}}, base.yaxis, {{title:{{text:'failures', font:{{size:10}} }} }}),
    xaxis: Object.assign({{}}, base.xaxis, {{tickangle: -30}}),
    margin: {{l:50, r:14, t:30, b:120}},
  }}),
  {{displayModeBar:false, responsive:true}}
);

Plotly.newPlot('chart_tools_vs_time',
  [
    {{
      x: sc_n.filter((_,i)=>!sc_err[i]),
      y: sc_t.filter((_,i)=>!sc_err[i]),
      text: sc_qid.filter((_,i)=>!sc_err[i]),
      mode:'markers', type:'scatter', name:'ok',
      marker:{{color:'#6ea8ff', size:6, opacity:0.7}},
    }},
    {{
      x: sc_n.filter((_,i)=>sc_err[i]),
      y: sc_t.filter((_,i)=>sc_err[i]),
      text: sc_qid.filter((_,i)=>sc_err[i]),
      mode:'markers', type:'scatter', name:'errored',
      marker:{{color:'#ff7373', size:7, opacity:0.9, symbol:'x'}},
    }},
  ],
  Object.assign({{}}, base, {{
    title: {{text:'Tool calls vs wall time (per question)', font:{{size:12, color:'#d6dae5'}} }},
    xaxis: Object.assign({{}}, base.xaxis, {{title:{{text:'tool calls', font:{{size:10}} }} }}),
    yaxis: Object.assign({{}}, base.yaxis, {{title:{{text:'seconds', font:{{size:10}} }} }}),
  }}),
  {{displayModeBar:false, responsive:true}}
);

document.getElementById('show_qstart').addEventListener('change', applyShapes);
document.getElementById('show_qend').addEventListener('change', applyShapes);
applyShapes();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        state = fetch_state()
        html = render_html(state)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


def main() -> None:
    print(f"Dashboard serving at http://localhost:{PORT}")
    print("Each refresh ssh's to orcd-login. Ctrl-C to stop.")
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
