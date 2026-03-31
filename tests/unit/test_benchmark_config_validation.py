"""
Unit tests for benchmarking config validation in ConfigurationManager.

Tests cover enum validation for modes, providers, and enabled_metrics.
"""

import pytest
import tempfile
from pathlib import Path

from src.cli.managers.config_manager import ConfigurationManager


def _make_manager():
    """Create a ConfigurationManager without loading config files."""
    mgr = object.__new__(ConfigurationManager)
    mgr.configs = []
    mgr.config = {}
    mgr.env = None
    return mgr


def _base_config(tmp_path, **overrides):
    """Build a minimal valid benchmarking config dict."""
    agent_md = tmp_path / "agent.md"
    agent_md.write_text("# Agent\nYou are a helpful assistant.")

    bench = {
        "agent_class": "QAPipeline",
        "agent_md_file": str(agent_md),
        "provider": "openai",
        "model": "gpt-4o",
        "modes": ["RAGAS"],
    }
    bench.update(overrides)
    return {
        "services": {"benchmarking": bench},
        "_config_path": str(tmp_path / "config.yaml"),
    }


class TestModeValidation:
    def test_valid_modes(self, tmp_path):
        config = _base_config(tmp_path, modes=["SOURCES", "RAGAS"])
        mgr = _make_manager()
        mgr._validate_benchmarking_config(config, ["benchmarking"])

    def test_invalid_mode_raises(self, tmp_path):
        config = _base_config(tmp_path, modes=["RAGAS", "INVALID_MODE"])
        mgr = _make_manager()
        with pytest.raises(ValueError, match="Invalid benchmarking mode"):
            mgr._validate_benchmarking_config(config, ["benchmarking"])

    def test_modes_must_be_list(self, tmp_path):
        config = _base_config(tmp_path, modes="RAGAS")
        mgr = _make_manager()
        with pytest.raises(ValueError, match="must be a list"):
            mgr._validate_benchmarking_config(config, ["benchmarking"])

    def test_empty_modes_ok(self, tmp_path):
        config = _base_config(tmp_path, modes=[])
        mgr = _make_manager()
        mgr._validate_benchmarking_config(config, ["benchmarking"])


class TestProviderValidation:
    @pytest.mark.parametrize("provider", ["openai", "ollama", "local", "huggingface", "anthropic"])
    def test_valid_providers(self, tmp_path, provider):
        overrides = {"provider": provider}
        if provider == "local":
            overrides["ollama_url"] = "http://localhost:11434"
        config = _base_config(tmp_path, **overrides)
        mgr = _make_manager()
        mgr._validate_benchmarking_config(config, ["benchmarking"])

    def test_invalid_provider_raises(self, tmp_path):
        config = _base_config(tmp_path, provider="bad_provider")
        mgr = _make_manager()
        with pytest.raises(ValueError, match="Invalid benchmarking provider"):
            mgr._validate_benchmarking_config(config, ["benchmarking"])


class TestMetricsValidation:
    def test_valid_metrics(self, tmp_path):
        config = _base_config(tmp_path)
        config["services"]["benchmarking"]["mode_settings"] = {
            "ragas_settings": {
                "enabled_metrics": ["answer_relevancy", "faithfulness"]
            }
        }
        mgr = _make_manager()
        mgr._validate_benchmarking_config(config, ["benchmarking"])

    def test_invalid_metric_raises(self, tmp_path):
        config = _base_config(tmp_path)
        config["services"]["benchmarking"]["mode_settings"] = {
            "ragas_settings": {
                "enabled_metrics": ["answer_relevancy", "bogus_metric"]
            }
        }
        mgr = _make_manager()
        with pytest.raises(ValueError, match="Invalid RAGAS metric"):
            mgr._validate_benchmarking_config(config, ["benchmarking"])

    def test_metrics_must_be_list(self, tmp_path):
        config = _base_config(tmp_path)
        config["services"]["benchmarking"]["mode_settings"] = {
            "ragas_settings": {
                "enabled_metrics": "answer_relevancy"
            }
        }
        mgr = _make_manager()
        with pytest.raises(ValueError, match="must be a list"):
            mgr._validate_benchmarking_config(config, ["benchmarking"])

    def test_no_metrics_ok(self, tmp_path):
        """When enabled_metrics is not specified, no validation error."""
        config = _base_config(tmp_path)
        mgr = _make_manager()
        mgr._validate_benchmarking_config(config, ["benchmarking"])


class TestSkipsNonBenchmarking:
    def test_no_benchmarking_service_skips(self, tmp_path):
        """Validation is skipped when 'benchmarking' not in services list."""
        config = {"services": {}}
        mgr = _make_manager()
        mgr._validate_benchmarking_config(config, ["chatbot"])
