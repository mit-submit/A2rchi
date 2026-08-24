"""archi.eval.report — aggregation, markdown render, cost rollups."""
import pytest

from archi.eval.engine import run_eval
from archi.eval.report import build_report, render_markdown, sum_llm_calls


def _report(smoke_atoms, arm, grader_cls, invoker_cls):
    run = run_eval(
        smoke_atoms,
        [arm],
        grader=grader_cls(),
        oracle_invoker=invoker_cls(),
        generation_id="gen:pin",
        dataset="qa_smoke.yaml",
        run_id="eval-fixed",
    )
    return build_report(run)


def test_report_header_and_arm_rollup(
    smoke_atoms, arm_cls, grader_cls, invoker_cls
):
    report = _report(
        smoke_atoms,
        arm_cls(
            generation_id="gen:pin",
            latency_ms=40,
            cost_usd=0.002,
            prompt_tokens=100,
            completion_tokens=25,
        ),
        grader_cls,
        invoker_cls,
    )
    assert report["run_id"] == "eval-fixed"
    assert report["dataset"] == "qa_smoke.yaml"
    assert report["generation_id"] == "gen:pin"
    assert report["generation_conflict"] is False
    (arm,) = report["arms"]
    assert arm["arm"] == "fake"
    assert arm["atoms"] == 7
    assert arm["status_counts"]["scored"] == 7
    assert arm["passed"] == 7 and arm["quality_accounted"] == 7
    assert arm["pass_rate"] == 1.0
    assert arm["mean_score"] == 1.0
    assert arm["latency_ms"] == {"count": 7, "mean": 40, "p50": 40, "max": 40}
    assert arm["tokens"] == {"prompt": 700, "completion": 175}
    assert arm["cost_usd"] == pytest.approx(0.014)
    assert arm["generation_ids"] == ["gen:pin"]
    assert len(arm["results"]) == 7


def test_report_quality_denominator_excludes_quarantined(
    smoke_atoms, arm_cls, answers
):
    # No oracle invoker: the live atom quarantines as oracle_failed and
    # must not dilute the pass rate; a missing scripted answer becomes
    # execution_failed and must count against it (PR #596 semantics);
    # no grader: the graded-only atom is ungraded, also excluded.
    answers.pop("live-open-downtimes")
    answers.pop("gt-name-shape")
    run = run_eval(smoke_atoms, [arm_cls(answers)])
    report = build_report(run)
    (arm,) = report["arms"]
    assert arm["status_counts"] == {
        "scored": 4,
        "execution_failed": 1,
        "evaluation_failed": 0,
        "ungraded": 1,
        "oracle_failed": 1,
        "answer_changed": 0,
    }
    assert arm["quality_accounted"] == 5  # scored + execution_failed
    assert arm["passed"] == 4
    assert arm["pass_rate"] == pytest.approx(0.8)
    assert arm["cost_usd"] is None  # nothing reported cost
    assert arm["tokens"] == {"prompt": None, "completion": None}


def test_report_multi_arm_comparison(
    smoke_atoms, arm_cls, grader_cls, invoker_cls, answers
):
    answers["cmssw-latest-14x"] = "wrong"
    run = run_eval(
        smoke_atoms,
        [arm_cls(name="arm-good"), arm_cls(answers, name="arm-bad")],
        grader=grader_cls(),
        oracle_invoker=invoker_cls(),
    )
    report = build_report(run)
    by_arm = {arm["arm"]: arm for arm in report["arms"]}
    assert by_arm["arm-good"]["pass_rate"] == 1.0
    assert by_arm["arm-bad"]["pass_rate"] == pytest.approx(6 / 7)


def test_markdown_render(smoke_atoms, arm_cls, grader_cls, invoker_cls):
    report = _report(
        smoke_atoms, arm_cls(generation_id="gen:pin"), grader_cls, invoker_cls
    )
    text = render_markdown(report)
    assert text.startswith("# Archi QA Evaluation Report")
    assert "`eval-fixed`" in text
    assert "Pinned generation: `gen:pin`" in text
    assert "## Arm `fake`" in text
    assert "`cmssw-latest-14x`: scored [pass] score=1.000" in text
    assert "conflict" not in text


def test_markdown_flags_generation_conflict(
    smoke_atoms, arm_cls, grader_cls, invoker_cls
):
    run = run_eval(
        smoke_atoms,
        [
            arm_cls(generation_id="gen:a", name="arm-a"),
            arm_cls(generation_id="gen:b", name="arm-b"),
        ],
        grader=grader_cls(),
        oracle_invoker=invoker_cls(),
    )
    text = render_markdown(build_report(run))
    assert "(conflict: arms disagree)" in text


def test_markdown_marks_failures_and_details(smoke_atoms, arm_cls, answers):
    answers["cmssw-latest-14x"] = "CMSSW_15_0_0"
    run = run_eval(smoke_atoms, [arm_cls(answers)])
    text = render_markdown(build_report(run))
    assert "`cmssw-latest-14x`: scored [FAIL] score=0.000" in text
    assert "`live-open-downtimes`: oracle_failed [-]" in text
    assert "no oracle invoker" in text


# --- okg.llm_calls cost helper (fake connection; the table contract is
# verified against the okg checkout's schema.sql, see report.py) ---


class FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, query, params):
        self.log.append((query, list(params)))

    def fetchone(self):
        return (12, 3400, 890, 4290, 0.0421, 1)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return FakeCursor(self.log)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_sum_llm_calls_query_and_sums():
    log = []
    totals = sum_llm_calls(
        "postgresql://example/db",
        since="2026-08-24T10:00:00+00:00",
        until="2026-08-24T10:05:00+00:00",
        deployment="cern-team",
        generation_id="gen:pin",
        connect=lambda dsn: FakeConnection(log),
    )
    assert totals == {
        "calls": 12,
        "prompt_tokens": 3400,
        "completion_tokens": 890,
        "total_tokens": 4290,
        "cost_usd": pytest.approx(0.0421),
        "failed_calls": 1,
    }
    (query, params) = log[0]
    assert "FROM okg.llm_calls" in query
    assert "ts >= %s" in query and "ts < %s" in query
    assert "deployment_name = %s" in query
    assert "generation_id = %s" in query
    assert params == [
        "2026-08-24T10:00:00+00:00",
        "2026-08-24T10:05:00+00:00",
        "cern-team",
        "gen:pin",
    ]


def test_sum_llm_calls_optional_filters_omitted():
    log = []
    sum_llm_calls(
        "postgresql://example/db",
        since="a",
        until="b",
        connect=lambda dsn: FakeConnection(log),
    )
    (query, params) = log[0]
    assert "deployment_name" not in query
    assert "generation_id" not in query
    assert params == ["a", "b"]
