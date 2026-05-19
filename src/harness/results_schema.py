"""Canonical benchmark results schema.

One pydantic model hierarchy used by both the harness (write side) and
the analysis scripts (read side). Schema is versioned so future changes
fail loudly instead of silently drifting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

ThinkingMode = Literal["on", "off", "n/a"]
TraceEventType = Literal[
    "llm_call",
    "retrieval",
    "tool_start",
    "tool_output",
    "tool_end",
    "thinking_start",
    "thinking_end",
    "text",
    "error",
]


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_type: TraceEventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: Optional[float] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class FailureRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_type: str
    message: str
    attempt: int


class QuestionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    question: str
    reference_answer: Optional[str] = None
    answer: Optional[str] = None
    thinking_content: Optional[str] = None
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    trace_events: List[TraceEvent] = Field(default_factory=list)
    sources_metadata: List[Dict[str, Any]] = Field(default_factory=list)
    sources_content: List[str] = Field(default_factory=list)
    wall_clock_ms: Optional[float] = None
    attempts: int = 1
    failures: List[FailureRecord] = Field(default_factory=list)
    status: Literal["ok", "failed", "empty", "timeout"] = "ok"


class RunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    config_id: str
    config_path: str
    model_id: str
    pipeline_id: str
    host: str
    git_sha: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    thinking_mode: ThinkingMode = "n/a"
    retry_max_attempts: int = 3
    parallel_workers: int = 1
    ollama_url: Optional[str] = None


class BenchmarkResult(BaseModel):
    """Top-level result artifact for one benchmark run.

    Emitted to `bench_out/<run_id>/run.json`. Individual question results
    may also be streamed to `bench_out/<run_id>/questions/<question_id>.json`
    for incremental durability.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    metadata: RunMetadata
    question_results: List[QuestionResult] = Field(default_factory=list)
    aggregate_token_usage: TokenUsage = Field(default_factory=TokenUsage)

    def recompute_aggregate(self) -> None:
        total = TokenUsage()
        for q in self.question_results:
            total = total + q.token_usage
        self.aggregate_token_usage = total

    @property
    def failed_question_ids(self) -> List[str]:
        return [q.question_id for q in self.question_results if q.status != "ok"]

    @property
    def successful_question_ids(self) -> List[str]:
        return [q.question_id for q in self.question_results if q.status == "ok"]
