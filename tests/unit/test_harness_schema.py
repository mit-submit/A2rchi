"""Unit tests for the canonical benchmark results schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.harness.results_schema import (
    SCHEMA_VERSION,
    BenchmarkResult,
    FailureRecord,
    QuestionResult,
    RunMetadata,
    TokenUsage,
    TraceEvent,
)


def _metadata(**overrides) -> RunMetadata:
    base = dict(
        run_id="test-run",
        config_id="cfg-a",
        config_path="configs/submit75/eval-gemma4-26b-optimized-thinking-off.yaml",
        model_id="gemma4:26b",
        pipeline_id="cms-comp-ops-agent",
        host="submit75.mit.edu",
    )
    base.update(overrides)
    return RunMetadata(**base)


def test_token_usage_addition():
    a = TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    b = TokenUsage(prompt_tokens=3, completion_tokens=7, total_tokens=10)
    c = a + b
    assert c.prompt_tokens == 13
    assert c.completion_tokens == 12
    assert c.total_tokens == 25


def test_question_result_defaults_clean():
    q = QuestionResult(question_id="q1", question="why is the sky blue?")
    assert q.status == "ok"
    assert q.attempts == 1
    assert q.failures == []
    assert q.token_usage.total_tokens == 0
    assert q.trace_events == []


def test_question_result_failure_shape():
    q = QuestionResult(
        question_id="q2",
        question="does the monit tool work?",
        answer=None,
        status="failed",
        attempts=3,
        failures=[
            FailureRecord(error_type="TimeoutError", message="exceeded 120s", attempt=1),
            FailureRecord(error_type="TimeoutError", message="exceeded 120s", attempt=2),
            FailureRecord(error_type="TimeoutError", message="exceeded 120s", attempt=3),
        ],
    )
    assert q.status == "failed"
    assert len(q.failures) == 3
    assert q.failures[-1].attempt == 3


def test_benchmark_result_roundtrip_preserves_schema_version():
    result = BenchmarkResult(
        metadata=_metadata(),
        question_results=[
            QuestionResult(
                question_id="q1",
                question="q1?",
                answer="a1",
                token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            ),
            QuestionResult(
                question_id="q2",
                question="q2?",
                answer="a2",
                token_usage=TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300),
            ),
        ],
    )
    result.recompute_aggregate()
    assert result.schema_version == SCHEMA_VERSION
    assert result.aggregate_token_usage.total_tokens == 450

    dumped = result.model_dump_json()
    reloaded = BenchmarkResult.model_validate_json(dumped)
    assert reloaded.schema_version == SCHEMA_VERSION
    assert reloaded.aggregate_token_usage.total_tokens == 450
    assert reloaded.metadata.host == "submit75.mit.edu"


def test_benchmark_result_rejects_unknown_top_level_key():
    with pytest.raises(ValidationError):
        BenchmarkResult.model_validate(
            {
                "schema_version": SCHEMA_VERSION,
                "metadata": _metadata().model_dump(),
                "question_results": [],
                "aggregate_token_usage": TokenUsage().model_dump(),
                "mystery_field": 42,
            }
        )


def test_run_metadata_rejects_unknown_thinking_mode():
    with pytest.raises(ValidationError):
        _metadata(thinking_mode="maybe")


def test_failed_and_successful_helpers():
    result = BenchmarkResult(
        metadata=_metadata(),
        question_results=[
            QuestionResult(question_id="a", question="?", status="ok"),
            QuestionResult(question_id="b", question="?", status="failed"),
            QuestionResult(question_id="c", question="?", status="empty"),
            QuestionResult(question_id="d", question="?", status="ok"),
        ],
    )
    assert result.successful_question_ids == ["a", "d"]
    assert result.failed_question_ids == ["b", "c"]


def test_trace_event_event_type_literal():
    with pytest.raises(ValidationError):
        TraceEvent(event_type="not_a_real_event")

    ev = TraceEvent(event_type="llm_call", duration_ms=123.4, payload={"model": "gemma4:26b"})
    assert ev.event_type == "llm_call"
    assert ev.duration_ms == 123.4
    assert ev.payload["model"] == "gemma4:26b"
