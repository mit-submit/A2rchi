"""Report building for archi.eval runs.

Aggregates an :class:`~archi.eval.engine.EvalRun` into per-arm and
per-atom results with cost + latency rollups, renders them as JSON or
markdown, and (optionally) sums a deployment's ``okg.llm_calls`` rows
over the run window for substrate-side cost accounting.

Provenance: the markdown shape (header pins, lifecycle counts, per-item
sections) and the pass-rate denominators port PR #596's ``scoring.py``
report half — quality-accounted attempts are ``scored +
execution_failed`` (an arm that crashes counts against it), while
quarantined results (``oracle_failed`` / ``answer_changed``, from PR
#608) and ``ungraded`` ones are excluded from rates and surfaced as
their own counts. New in v3: token/cost/latency rollups read straight
off the AnswerRecords, the generation-pin header, and the
``okg.llm_calls`` helper.

The cost helper's table contract was verified against the okg checkout
(``src/okg/substrate/db/schema.sql``): ``okg.llm_calls`` with columns
``ts``, ``deployment_name``, ``caller``, ``model``, ``prompt_tokens``,
``completion_tokens``, ``total_tokens``, ``cost_usd``, ``latency_ms``,
``success``, ``generation_id``. If that table moves, update
``LLM_CALLS_TABLE``/query below and this note.
"""
from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any, Callable, Dict, List, Optional

from .engine import RESULT_STATUSES, ArmRun, EvalRun

LLM_CALLS_TABLE = "okg.llm_calls"


def _rate(value: Optional[float]) -> str:
    return "unavailable" if value is None else f"{value:.3f}"


def _aggregate_arm(arm_run: ArmRun) -> Dict[str, Any]:
    counts = Counter(result.status for result in arm_run.results)
    quality = counts["scored"] + counts["execution_failed"]
    passed = sum(1 for result in arm_run.results if result.passed)
    scores = [
        result.score for result in arm_run.results if result.score is not None
    ]
    records = [
        result.record for result in arm_run.results if result.record is not None
    ]
    latencies = [
        record.latency_ms for record in records if record.latency_ms is not None
    ]
    prompt_tokens = [
        record.prompt_tokens for record in records if record.prompt_tokens is not None
    ]
    completion_tokens = [
        record.completion_tokens
        for record in records
        if record.completion_tokens is not None
    ]
    costs = [record.cost_usd for record in records if record.cost_usd is not None]
    return {
        "arm": arm_run.arm,
        "description": arm_run.description,
        "atoms": len(arm_run.results),
        "status_counts": {
            status: counts.get(status, 0) for status in RESULT_STATUSES
        },
        "passed": passed,
        "quality_accounted": quality,
        "pass_rate": passed / quality if quality else None,
        "mean_score": sum(scores) / len(scores) if scores else None,
        "latency_ms": {
            "count": len(latencies),
            "mean": sum(latencies) / len(latencies) if latencies else None,
            "p50": median(latencies) if latencies else None,
            "max": max(latencies) if latencies else None,
        },
        "tokens": {
            "prompt": sum(prompt_tokens) if prompt_tokens else None,
            "completion": sum(completion_tokens) if completion_tokens else None,
        },
        "cost_usd": sum(costs) if costs else None,
        "generation_ids": list(arm_run.generation_ids),
        "results": [result.to_dict() for result in arm_run.results],
    }


def build_report(run: EvalRun) -> Dict[str, Any]:
    """Aggregate a finished run into the canonical report dict."""
    return {
        "run_id": run.run_id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "dataset": run.dataset,
        "generation_id": run.generation_id,
        "generation_conflict": run.generation_conflict,
        "arms": [_aggregate_arm(arm_run) for arm_run in run.arm_runs],
    }


def render_markdown(report: Dict[str, Any]) -> str:
    """Render the report dict as markdown (PR #596 report shape)."""
    lines = [
        "# Archi QA Evaluation Report",
        "",
        f"- Run: `{report['run_id']}`",
        f"- Window: `{report['started_at']}` -> `{report['finished_at']}`",
        f"- Dataset: `{report['dataset'] or 'unavailable'}`",
        f"- Pinned generation: `{report['generation_id'] or 'unavailable'}`"
        + (" **(conflict: arms disagree)**" if report["generation_conflict"] else ""),
        "",
    ]
    for arm in report["arms"]:
        counts = arm["status_counts"]
        latency = arm["latency_ms"]
        tokens = arm["tokens"]
        cost = arm["cost_usd"]
        lines.extend(
            [
                f"## Arm `{arm['arm']}`",
                "",
                f"- {arm['description']}",
                f"- Pass rate: `{_rate(arm['pass_rate'])}` "
                f"({arm['passed']}/{arm['quality_accounted']} quality-accounted)",
                f"- Mean score: `{_rate(arm['mean_score'])}`",
                f"- Statuses: `{ {s: n for s, n in counts.items() if n} }`",
                f"- Latency ms (mean/p50/max over {latency['count']}): "
                f"`{latency['mean'] if latency['mean'] is None else round(latency['mean'])}"
                f" / {latency['p50']} / {latency['max']}`",
                f"- Tokens prompt/completion: "
                f"`{tokens['prompt']} / {tokens['completion']}`; "
                f"cost USD: `{'unavailable' if cost is None else f'{cost:.4f}'}`",
                (
                    f"- Generations observed: `{', '.join(arm['generation_ids'])}`"
                    if arm["generation_ids"]
                    else "- Generations observed: `none reported`"
                ),
                "",
                "### Per-atom results",
                "",
            ]
        )
        for result in arm["results"]:
            passed = result.get("passed")
            marker = "pass" if passed else ("FAIL" if passed is False else "-")
            score = result.get("score")
            rendered_score = "" if score is None else f" score={score:.3f}"
            detail = result.get("detail")
            rendered_detail = f" — {detail}" if detail else ""
            lines.append(
                f"- `{result['atom_id']}`: {result['status']} "
                f"[{marker}]{rendered_score}{rendered_detail}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def sum_llm_calls(
    dsn: str,
    *,
    since: str,
    until: str,
    deployment: Optional[str] = None,
    generation_id: Optional[str] = None,
    connect: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Sum ``okg.llm_calls`` rows for a run window (substrate cost).

    ``since``/``until`` are the run's ``started_at``/``finished_at``
    (ISO timestamps; the window is ``ts >= since AND ts < until``).
    ``connect`` is injectable for tests; by default ``psycopg`` is
    imported lazily — it ships with the okg host environment archi is
    installed into, and is deliberately not an archi dependency.
    """
    if connect is None:
        try:
            import psycopg  # okg host dependency; not vendored here
        except ImportError as exc:  # pragma: no cover - env-specific
            raise RuntimeError(
                "sum_llm_calls needs psycopg, which comes with the okg host "
                "environment; run from an okg-bearing interpreter or inject "
                "connect="
            ) from exc
        connect = psycopg.connect
    clauses = ["ts >= %s", "ts < %s"]
    params: List[Any] = [since, until]
    if deployment is not None:
        clauses.append("deployment_name = %s")
        params.append(deployment)
    if generation_id is not None:
        clauses.append("generation_id = %s")
        params.append(generation_id)
    query = (
        "SELECT count(*), coalesce(sum(prompt_tokens), 0), "
        "coalesce(sum(completion_tokens), 0), coalesce(sum(total_tokens), 0), "
        "coalesce(sum(cost_usd), 0), "
        "count(*) FILTER (WHERE NOT success) "
        f"FROM {LLM_CALLS_TABLE} WHERE " + " AND ".join(clauses)
    )
    with connect(dsn) as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            row = cursor.fetchone()
    return {
        "calls": row[0],
        "prompt_tokens": row[1],
        "completion_tokens": row[2],
        "total_tokens": row[3],
        "cost_usd": float(row[4]),
        "failed_calls": row[5],
    }
