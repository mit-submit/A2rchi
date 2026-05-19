"""Eval config loader with pydantic validation.

Replaces the ad-hoc YAML loading in `service_benchmark.py` and the
`scripts/*.py` scripts. One canonical schema for a benchmark eval
config with strict validation (unknown keys are rejected, no silent
drift between sibling configs) and one canonical key per setting.

Supports Jinja2 rendering when the config file is a `.yaml.j2`
template, so a base template can be shared across configs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.harness.results_schema import ThinkingMode

PipelineType = Literal[
    "cms_comp_ops_agent",
    "cms_comp_ops_agent_no_tools",
    "qa_rag",
    "bare_llm",
    "copilot_agent",
]

Condition = Literal["optimized-tools", "no-tools", "rag-only", "bare-llm"]


class RetryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_attempts: int = 3
    base_delay_s: float = 2.0
    max_delay_s: float = 30.0
    retryable_errors: List[str] = Field(
        default_factory=lambda: ["TimeoutError", "ConnectionError", "HTTPError5xx"]
    )

    @field_validator("max_attempts")
    @classmethod
    def _positive_attempts(cls, v: int) -> int:
        if v < 1:
            raise ValueError("retry.max_attempts must be >= 1")
        return v


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    provider: Literal["ollama", "openai", "anthropic"] = "ollama"
    ollama_url: Optional[str] = None
    temperature: float = 0.0
    context_window: Optional[int] = None


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: PipelineType
    tools_enabled: bool = True
    retrieval_enabled: bool = True


class DataSources(BaseModel):
    """Enough to detect sweep-mode DM reuse: two configs with equal DataSources share a DM."""

    model_config = ConfigDict(extra="forbid")

    jira: bool = False
    redmine: bool = False
    links: List[str] = Field(default_factory=list)
    git_repos: List[str] = Field(default_factory=list)

    def fingerprint(self) -> str:
        """Stable string identity; equal fingerprint -> DM can be shared."""
        return yaml.safe_dump(self.model_dump(), sort_keys=True)


class EvalConfig(BaseModel):
    """Canonical per-run benchmark eval config."""

    model_config = ConfigDict(extra="forbid")

    name: str
    config_id: str
    condition: Condition
    thinking_mode: ThinkingMode = "n/a"
    parallel_workers: int = 1
    model: ModelConfig
    pipeline: PipelineConfig
    retry: RetryConfig = Field(default_factory=RetryConfig)
    data_sources: DataSources = Field(default_factory=DataSources)
    questions_path: str
    timeout_s: float = 300.0
    notes: Optional[str] = None

    @field_validator("parallel_workers")
    @classmethod
    def _positive_workers(cls, v: int) -> int:
        if v < 1:
            raise ValueError("parallel_workers must be >= 1")
        return v


def _load_yaml_text(path: Path) -> Dict[str, Any]:
    text = path.read_text()
    if path.suffix == ".j2" or path.name.endswith(".yaml.j2"):
        try:
            from jinja2 import Environment, FileSystemLoader, StrictUndefined
        except ImportError as e:
            raise RuntimeError(
                f"Jinja2 is required to load templated config {path}. Install with `pip install jinja2`."
            ) from e
        env = Environment(
            loader=FileSystemLoader(str(path.parent)),
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        template = env.from_string(text)
        text = template.render()
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"Config at {path} did not parse to a mapping")
    return data


def load_eval_config(path: Path | str) -> EvalConfig:
    """Load and validate an eval config. Raises pydantic.ValidationError on drift."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"eval config not found: {p}")
    data = _load_yaml_text(p)
    return EvalConfig.model_validate(data)
