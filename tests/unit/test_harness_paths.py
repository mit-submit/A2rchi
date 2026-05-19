"""Unit tests for `HarnessPaths`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.harness.paths import HarnessPaths


@pytest.fixture(autouse=True)
def _clear_archi_env(monkeypatch):
    for key in ("ARCHI_REPO_ROOT", "ARCHI_BENCH_OUT", "ARCHI_CONFIG_ROOT", "ARCHI_RUN_ID"):
        monkeypatch.delenv(key, raising=False)


def test_default_paths_resolve_under_repo_root():
    paths = HarnessPaths.default(run_id="test-run")
    assert paths.run_id == "test-run"
    assert paths.repo_root.exists()
    assert paths.bench_out == paths.repo_root / "bench_out"
    assert paths.config_root == paths.repo_root / "configs"
    assert paths.run_dir == paths.bench_out / "test-run"
    assert paths.questions_dir == paths.run_dir / "questions"
    assert paths.checkpoints_dir == paths.run_dir / "checkpoints"
    assert paths.run_json == paths.run_dir / "run.json"
    assert paths.question_json("q42") == paths.questions_dir / "q42.json"


def test_env_override_bench_out(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ARCHI_BENCH_OUT", str(tmp_path / "custom_bench"))
    paths = HarnessPaths.default(run_id="r1")
    assert paths.bench_out == tmp_path / "custom_bench"
    assert paths.run_dir == tmp_path / "custom_bench" / "r1"


def test_env_override_repo_root(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ARCHI_REPO_ROOT", str(tmp_path))
    paths = HarnessPaths.default(run_id="r1")
    assert paths.repo_root == tmp_path
    # bench_out and config_root derive from repo_root by default
    assert paths.bench_out == tmp_path / "bench_out"
    assert paths.config_root == tmp_path / "configs"


def test_run_id_from_env(monkeypatch):
    monkeypatch.setenv("ARCHI_RUN_ID", "from-env-run")
    paths = HarnessPaths.default()
    assert paths.run_id == "from-env-run"


def test_auto_run_id_when_unset():
    paths = HarnessPaths.default()
    # UTC format YYYYMMDDTHHMMSSZ
    assert paths.run_id.endswith("Z")
    assert "T" in paths.run_id
    assert len(paths.run_id) == 16


def test_ensure_run_dirs_creates_tree(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ARCHI_BENCH_OUT", str(tmp_path / "bench"))
    paths = HarnessPaths.default(run_id="smoke")
    paths.ensure_run_dirs()
    assert paths.run_dir.is_dir()
    assert paths.questions_dir.is_dir()
    assert paths.checkpoints_dir.is_dir()


def test_config_for_host(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ARCHI_CONFIG_ROOT", str(tmp_path / "configs"))
    paths = HarnessPaths.default(run_id="r")
    assert paths.config_for_host("submit75") == tmp_path / "configs" / "submit75"
    assert paths.config_for_host("submit76") == tmp_path / "configs" / "submit76"


def test_defaults_do_not_point_at_legacy_root_archi(monkeypatch):
    """Regression guard: defaults must not resolve to the legacy hardcoded `/root/archi` tree."""
    for key in ("ARCHI_REPO_ROOT", "ARCHI_BENCH_OUT", "ARCHI_CONFIG_ROOT"):
        monkeypatch.delenv(key, raising=False)
    paths = HarnessPaths.default(run_id="r")
    assert not str(paths.bench_out).startswith("/root/archi")
    assert not str(paths.repo_root).startswith("/root/archi")
    assert not str(paths.config_root).startswith("/root/archi")
