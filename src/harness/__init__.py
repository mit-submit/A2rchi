"""Shared benchmark harness.

Canonical home for reusable benchmark logic: results schema (pydantic),
token usage normalization, path resolution, rubric loading, config
validation. Entrypoints and analysis scripts import from here instead of
reimplementing the same logic.

See openspec/changes/organize-benchmark-workspace/ for the design.
"""

from src.harness.config_loader import (
    Condition,
    DataSources,
    EvalConfig,
    ModelConfig,
    PipelineConfig,
    PipelineType,
    RetryConfig,
    load_eval_config,
)
from src.harness.paths import HarnessPaths
from src.harness.result_io import (
    find_failed_question_ids,
    find_successful_question_ids,
    read_question_result,
    scan_run_questions,
    write_question_result,
)
from src.harness.retry import (
    RetryOutcome,
    classify_exception,
    run_with_retry,
)
from src.harness.results_schema import (
    SCHEMA_VERSION,
    BenchmarkResult,
    FailureRecord,
    QuestionResult,
    RunMetadata,
    ThinkingMode,
    TokenUsage,
    TraceEvent,
    TraceEventType,
)
from src.harness.rubric import Criterion, Rubric, default_rubric, load_rubric
from src.harness.token_normalizer import normalize_token_usage, sum_token_usage

__all__ = [
    # schema
    "SCHEMA_VERSION",
    "BenchmarkResult",
    "QuestionResult",
    "TokenUsage",
    "TraceEvent",
    "TraceEventType",
    "RunMetadata",
    "FailureRecord",
    "ThinkingMode",
    # token normalizer
    "normalize_token_usage",
    "sum_token_usage",
    # config loader
    "Condition",
    "DataSources",
    "EvalConfig",
    "ModelConfig",
    "PipelineConfig",
    "PipelineType",
    "RetryConfig",
    "load_eval_config",
    # paths
    "HarnessPaths",
    # result IO
    "write_question_result",
    "read_question_result",
    "scan_run_questions",
    "find_failed_question_ids",
    "find_successful_question_ids",
    # retry
    "RetryOutcome",
    "classify_exception",
    "run_with_retry",
    # rubric
    "Criterion",
    "Rubric",
    "default_rubric",
    "load_rubric",
]
