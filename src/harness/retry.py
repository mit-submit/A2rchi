"""Per-question retry with exponential backoff.

Wraps a single-question pipeline invocation and retries transient
failures (timeout, connection error, HTTP 5xx) up to the configured
max attempts before recording a permanent failure.

The caller supplies a zero-arg callable that returns a `QuestionResult`.
`run_with_retry` returns a `RetryOutcome` containing either the final
`QuestionResult` or `None` if every attempt failed, plus the list of
`FailureRecord`s and the final `attempts` count.

Errors are classified as "retryable" or "permanent" based on the error
class name. The caller's `RetryConfig.retryable_errors` is a list of
simple names (`TimeoutError`, `ConnectionError`, `HTTPError5xx`) that is
matched against both the exception class name and any status-code
attribute for HTTP errors.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, List, Literal, Optional

from src.harness.config_loader import RetryConfig
from src.harness.results_schema import FailureRecord, QuestionResult

Classification = Literal["retryable", "permanent"]


def _http_status_from(exc: BaseException) -> Optional[int]:
    """Best-effort extraction of an HTTP status code from a variety of exception shapes."""
    for attr in ("status_code", "status", "code"):
        val = getattr(exc, attr, None)
        if isinstance(val, int):
            return val
    response = getattr(exc, "response", None)
    if response is not None:
        for attr in ("status_code", "status"):
            val = getattr(response, attr, None)
            if isinstance(val, int):
                return val
    return None


def classify_exception(exc: BaseException, policy: RetryConfig) -> Classification:
    """Decide whether an exception is retryable given the policy."""
    name = type(exc).__name__
    retryable = set(policy.retryable_errors)

    if name in retryable:
        return "retryable"

    # HTTPError5xx sentinel: match any exception whose http status is 5xx
    if "HTTPError5xx" in retryable:
        status = _http_status_from(exc)
        if status is not None and 500 <= status < 600:
            return "retryable"

    # Common base-class matches for convenience
    for base in type(exc).__mro__:
        if base.__name__ in retryable:
            return "retryable"

    return "permanent"


@dataclass
class RetryOutcome:
    result: Optional[QuestionResult]
    failures: List[FailureRecord] = field(default_factory=list)
    attempts: int = 0

    @property
    def succeeded(self) -> bool:
        return self.result is not None and self.result.status == "ok"


def run_with_retry(
    fn: Callable[[], QuestionResult],
    policy: RetryConfig,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> RetryOutcome:
    """Run `fn` up to `policy.max_attempts` times.

    Semantics:

    - Returns the first `QuestionResult` where `status == "ok"`, with
      `attempts` set to the attempt count and any prior failures
      recorded in `result.failures`.
    - If the pipeline raises a retryable exception, the exception is
      recorded as a FailureRecord and the next attempt is scheduled
      after an exponentially-backed-off sleep capped at
      `policy.max_delay_s`.
    - If the pipeline raises a permanent exception, the attempt loop
      stops immediately and the outcome contains no result.
    - If the pipeline returns a result with `status != "ok"` the loop
      treats that as a retryable outcome (the caller may have
      classified a soft failure internally, e.g. empty answer) and
      retries unless max_attempts is reached.
    - After `max_attempts` exhausted, outcome.result is None.
    """
    failures: List[FailureRecord] = []
    last_result: Optional[QuestionResult] = None

    for attempt in range(1, policy.max_attempts + 1):
        try:
            result = fn()
        except BaseException as exc:  # noqa: BLE001 - we re-raise permanent failures
            kind = classify_exception(exc, policy)
            failures.append(
                FailureRecord(
                    error_type=type(exc).__name__,
                    message=str(exc),
                    attempt=attempt,
                )
            )
            if kind == "permanent":
                return RetryOutcome(result=None, failures=failures, attempts=attempt)
            if attempt >= policy.max_attempts:
                return RetryOutcome(result=None, failures=failures, attempts=attempt)
            _sleep_backoff(policy, attempt, sleep_fn)
            continue

        last_result = result
        if result.status == "ok":
            # Preserve the failure trail so callers can see that a recovered
            # run had prior attempts.
            merged_failures = list(result.failures) + failures
            result_with_trail = result.model_copy(
                update={
                    "attempts": attempt,
                    "failures": merged_failures,
                }
            )
            return RetryOutcome(
                result=result_with_trail,
                failures=merged_failures,
                attempts=attempt,
            )

        # Soft failure returned by the pipeline (status != ok).
        failures.append(
            FailureRecord(
                error_type="SoftFailure",
                message=f"pipeline returned status={result.status}",
                attempt=attempt,
            )
        )
        if attempt >= policy.max_attempts:
            merged_failures = list(result.failures) + failures
            soft_final = result.model_copy(
                update={
                    "attempts": attempt,
                    "failures": merged_failures,
                }
            )
            return RetryOutcome(
                result=soft_final,
                failures=merged_failures,
                attempts=attempt,
            )
        _sleep_backoff(policy, attempt, sleep_fn)

    # Unreachable: loop always returns.
    return RetryOutcome(result=last_result, failures=failures, attempts=policy.max_attempts)


def _sleep_backoff(policy: RetryConfig, attempt: int, sleep_fn: Callable[[float], None]) -> None:
    delay = policy.base_delay_s * (2 ** (attempt - 1))
    delay = min(delay, policy.max_delay_s)
    sleep_fn(delay)
