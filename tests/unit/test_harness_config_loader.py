"""Unit tests for the eval config loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from src.harness.config_loader import (
    DataSources,
    EvalConfig,
    load_eval_config,
)


def _minimal_dict(**overrides):
    base = {
        "name": "gemma4-26b-optimized-thinking-off",
        "config_id": "submit75/gemma4-26b/optimized-tools/thinking-off",
        "condition": "optimized-tools",
        "thinking_mode": "off",
        "parallel_workers": 2,
        "model": {
            "model_id": "gemma4:26b",
            "provider": "ollama",
            "ollama_url": "http://localhost:11434",
        },
        "pipeline": {
            "type": "cms_comp_ops_agent",
            "tools_enabled": True,
            "retrieval_enabled": True,
        },
        "data_sources": {"jira": True, "redmine": False},
        "questions_path": "configs/submit75/curated_questions.json",
    }
    base.update(overrides)
    return base


def test_minimal_valid_config():
    cfg = EvalConfig.model_validate(_minimal_dict())
    assert cfg.model.model_id == "gemma4:26b"
    assert cfg.pipeline.type == "cms_comp_ops_agent"
    assert cfg.thinking_mode == "off"
    assert cfg.retry.max_attempts == 3
    assert cfg.parallel_workers == 2


def test_unknown_top_level_key_rejected():
    bad = _minimal_dict()
    bad["mystery"] = "oops"
    with pytest.raises(ValidationError):
        EvalConfig.model_validate(bad)


def test_invalid_condition_rejected():
    with pytest.raises(ValidationError):
        EvalConfig.model_validate(_minimal_dict(condition="kitchen-sink"))


def test_invalid_thinking_mode_rejected():
    with pytest.raises(ValidationError):
        EvalConfig.model_validate(_minimal_dict(thinking_mode="maybe"))


def test_parallel_workers_must_be_positive():
    with pytest.raises(ValidationError):
        EvalConfig.model_validate(_minimal_dict(parallel_workers=0))


def test_retry_max_attempts_must_be_positive():
    bad = _minimal_dict()
    bad["retry"] = {"max_attempts": 0}
    with pytest.raises(ValidationError):
        EvalConfig.model_validate(bad)


def test_data_sources_fingerprint_equal_for_equivalent_configs():
    a = EvalConfig.model_validate(_minimal_dict())
    b = EvalConfig.model_validate(_minimal_dict(name="different-name"))
    assert a.data_sources.fingerprint() == b.data_sources.fingerprint()


def test_data_sources_fingerprint_differs_on_source_change():
    a = EvalConfig.model_validate(_minimal_dict())
    b_dict = _minimal_dict()
    b_dict["data_sources"] = {"jira": True, "redmine": True}
    b = EvalConfig.model_validate(b_dict)
    assert a.data_sources.fingerprint() != b.data_sources.fingerprint()


def test_load_eval_config_from_yaml(tmp_path: Path):
    import yaml as yamllib

    cfg_path = tmp_path / "eval-test.yaml"
    cfg_path.write_text(yamllib.safe_dump(_minimal_dict()))
    cfg = load_eval_config(cfg_path)
    assert cfg.model.model_id == "gemma4:26b"
    assert cfg.pipeline.tools_enabled is True


def test_load_eval_config_from_jinja_template_renders_static(tmp_path: Path):
    """A .yaml.j2 template with no variables should still load and validate."""
    base = tmp_path / "base.yaml.j2"
    base.write_text(
        """
name: gemma4-26b-optimized-thinking-off
config_id: submit75/gemma4-26b/optimized-tools/thinking-off
condition: optimized-tools
thinking_mode: "off"
parallel_workers: 1
model:
  model_id: "gemma4:26b"
  provider: ollama
  ollama_url: "http://localhost:11434"
pipeline:
  type: cms_comp_ops_agent
  tools_enabled: true
  retrieval_enabled: true
data_sources:
  jira: true
questions_path: "configs/submit75/curated_questions.json"
""".strip()
    )
    cfg = load_eval_config(base)
    assert cfg.name == "gemma4-26b-optimized-thinking-off"
    assert cfg.model.model_id == "gemma4:26b"


def test_load_eval_config_jinja_with_undefined_variable_raises(tmp_path: Path):
    """StrictUndefined should raise when a template references an undefined variable."""
    import jinja2

    base = tmp_path / "base.yaml.j2"
    base.write_text("name: {{ undefined_var }}\n")
    with pytest.raises(jinja2.exceptions.UndefinedError):
        load_eval_config(base)


def test_load_eval_config_file_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_eval_config(tmp_path / "nope.yaml")


def test_datasources_default_empty_is_valid():
    d = DataSources()
    assert d.jira is False
    assert d.git_repos == []
    assert d.fingerprint()  # non-empty yaml string
