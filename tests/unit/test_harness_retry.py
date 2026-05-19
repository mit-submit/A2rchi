"""Unit tests for `run_with_retry`, `classify_exception`, and retry integration."""

from __future__ import annotations

from typing import List

import pytest

from src.harness.config_loader import RetryConfig
from src.harness.results_schema import FailureRecord, QuestionResult, TokenUsage
from src.harness.retry import RetryOutcome, classify_exception, run_with_retry


def _ok(q_id: str = "q1", answer: str = "fine") -> QuestionResult:
    return QuestionResult(
        question_id=q_id,
        question="why is the sky blue?",
        answer=answer,
        token_usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        status="ok",
    )


def _failed(q_id: str = "q1") -> QuestionResult:
    return QuestionResult(
        question_id=q_id,
        question="why is the sky blue?",
        answer=None,
        status="failed",
    )


def _empty(q_id: str = "q1") -> QuestionResult:
    return QuestionResult(
        question_id=q_id,
        question="why is the sky blue?",
        answer=None,
        status="empty",
    )


class _FakeHttpError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"http {status_code}")
        self.status_code = status_code


class _FakeNestedHttpError(Exception):
    def __init__(self, status_code: int):
        super().__init__("nested")
        self.response = type("R", (), {"status_code": status_code})()


def _policy(**overrides) -> RetryConfig:
    base = dict(max_attempts=3, base_delay_s=0.0, max_delay_s=0.0)
    base.update(overrides)
    return RetryConfig(**base)


# ---------------------------------------------------------------------------
# classify_exception
# ---------------------------------------------------------------------------

def test_classify_retryable_by_name():
    policy = _policy()
    assert classify_exception(TimeoutError("x"), policy) == "retryable"
    assert classify_exception(ConnectionError("x"), policy) == "retryable"


def test_classify_permanent_by_name():
    policy = _policy()
    assert classify_exception(ValueError("bad config"), policy) == "permanent"
    assert classify_exception(KeyError("missing"), policy) == "permanent"


def test_classify_http_5xx_is_retryable():
    policy = _policy()
    assert classify_exception(_FakeHttpError(500), policy) == "retryable"
    assert classify_exception(_FakeHttpError(503), policy) == "retryable"


def test_classify_http_4xx_is_permanent():
    policy = _policy()
    assert classify_exception(_FakeHttpError(404), policy) == "permanent"
    assert classify_exception(_FakeHttpError(400), policy) == "permanent"


def test_classify_nested_http_status():
    policy = _policy()
    assert classify_exception(_FakeNestedHttpError(502), policy) == "retryable"


def test_classify_honors_custom_policy_list():
    policy = _policy(retryable_errors=["ValueError"])
    assert classify_exception(ValueError("x"), policy) == "retryable"
    assert classify_exception(TimeoutError("x"), policy) == "permanent"


# ---------------------------------------------------------------------------
# run_with_retry — success paths
# ---------------------------------------------------------------------------

def test_success_on_first_attempt():
    sleeps: List[float] = []
    outcome = run_with_retry(
        lambda: _ok(),
        _policy(),
        sleep_fn=sleeps.append,
    )
    assert outcome.succeeded
    assert outcome.attempts == 1
    assert outcome.result.attempts == 1
    assert outcome.result.failures == []
    assert sleeps == []  # no backoff on first-try success


def test_success_after_one_transient_failure():
    sleeps: List[float] = []
    call_count = {"n": 0}

    def flaky():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise TimeoutError("first try")
        return _ok()

    outcome = run_with_retry(flaky, _policy(base_delay_s=1.0, max_delay_s=10.0), sleep_fn=sleeps.append)
    assert outcome.succeeded
    assert outcome.attempts == 2
    assert outcome.result.attempts == 2
    assert len(outcome.result.failures) == 1
    assert outcome.result.failures[0].error_type == "TimeoutError"
    assert outcome.result.failures[0].attempt == 1
    assert sleeps == [1.0]  # one backoff between try 1 and try 2


def test_backoff_is_exponential_capped():
    sleeps: List[float] = []
    call_count = {"n": 0}

    def flaky():
        call_count["n"] += 1
        if call_count["n"] < 4:
            raise TimeoutError("no")
        return _ok()

    policy = _policy(max_attempts=5, base_delay_s=1.0, max_delay_s=3.0)
    outcome = run_with_retry(flaky, policy, sleep_fn=sleeps.append)
    assert outcome.succeeded
    assert outcome.attempts == 4
    # Sleeps at attempts 1,2,3: 1, 2, 3 (capped at max_delay_s=3)
    assert sleeps == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# run_with_retry — failure paths
# ---------------------------------------------------------------------------

def test_exhaust_retries_on_persistent_transient():
    def always_timeout():
        raise TimeoutError("nope")

    outcome = run_with_retry(always_timeout, _policy(max_attempts=3), sleep_fn=lambda _: None)
    assert not outcome.succeeded
    assert outcome.result is None
    assert outcome.attempts == 3
    assert len(outcome.failures) == 3
    assert all(f.error_type == "TimeoutError" for f in outcome.failures)


def test_permanent_error_stops_immediately():
    sleeps: List[float] = []

    def bad_config():
        raise ValueError("config is bad")

    outcome = run_with_retry(bad_config, _policy(max_attempts=5), sleep_fn=sleeps.append)
    assert not outcome.succeeded
    assert outcome.attempts == 1
    assert len(outcome.failures) == 1
    assert outcome.failures[0].error_type == "ValueError"
    assert sleeps == []  # no backoff on permanent failure


def test_soft_failure_result_is_retried():
    sleeps: List[float] = []
    call_count = {"n": 0}

    def returning_failed_then_ok():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _failed()
        return _ok()

    outcome = run_with_retry(
        returning_failed_then_ok,
        _policy(base_delay_s=1.0, max_delay_s=10.0),
        sleep_fn=sleeps.append,
    )
    assert outcome.succeeded
    assert outcome.attempts == 2
    # SoftFailure recorded on attempt 1
    types = [f.error_type for f in outcome.result.failures]
    assert "SoftFailure" in types
    assert sleeps == [1.0]


def test_soft_failure_exhaust_returns_last_soft_result():
    def always_empty():
        return _empty()

    outcome = run_with_retry(always_empty, _policy(max_attempts=2), sleep_fn=lambda _: None)
    assert outcome.result is not None
    assert outcome.result.status == "empty"
    assert outcome.result.attempts == 2
    assert len(outcome.result.failures) == 2
    assert all(f.error_type == "SoftFailure" for f in outcome.result.failures)
    assert outcome.succeeded is False  # soft failure is not "ok"


# ---------------------------------------------------------------------------
# integration
# ---------------------------------------------------------------------------

def test_result_with_prior_failures_preserved_on_success():
    """If the pipeline itself returns a QuestionResult that already has failures recorded
    (e.g. internal retries), those failures should be preserved alongside the retry-wrapper's."""

    call_count = {"n": 0}

    def flaky():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise TimeoutError("outer")
        return QuestionResult(
            question_id="q1",
            question="?",
            answer="final",
            status="ok",
            failures=[FailureRecord(error_type="InnerRetry", message="inner", attempt=1)],
        )

    outcome = run_with_retry(flaky, _policy(base_delay_s=0.0), sleep_fn=lambda _: None)
    assert outcome.succeeded
    # Outer retry (TimeoutError) + inner failure from the pipeline
    types = [f.error_type for f in outcome.result.failures]
    assert "TimeoutError" in types
    assert "InnerRetry" in types
    assert outcome.result.attempts == 2
