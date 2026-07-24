"""Unit tests for TemplateManager._stage_mcp_copy.

Covers the three build_context cases that determine deployment self-containment:
  * external/absolute context -> copied into mcp_build/ and path rewritten;
  * archi_code-relative context -> left alone (shipped via copy_source_code);
  * image-based sidecar -> untouched.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment

from src.cli.managers.templates_manager import TemplateManager


class _FakeConfigManager:
    def __init__(self, config):
        self.config = config
        self.configs = [config]

    def get_configs(self):
        return self.configs


def _make_context(base_dir, config):
    return SimpleNamespace(base_dir=base_dir, config_manager=_FakeConfigManager(config))


def _manager():
    return TemplateManager(Environment(), verbosity=0)


def _make_source(tmp_path, name="indico"):
    src = tmp_path / "external" / name
    src.mkdir(parents=True)
    (src / "Dockerfile").write_text("FROM python:3.11-slim\n")
    (src / "entrypoint.py").write_text("print('hi')\n")
    return src


def test_external_build_context_is_copied_and_rewritten(tmp_path):
    base_dir = tmp_path / "archi-demo"
    base_dir.mkdir()
    src = _make_source(tmp_path)

    config = {"mcp_servers": {"indico": {"build_context": str(src)}}}
    ctx = _make_context(base_dir, config)

    _manager()._stage_mcp_copy(ctx)

    # Source files landed under the deployment, build_context now points there.
    assert (base_dir / "mcp_build" / "indico" / "Dockerfile").is_file()
    assert (base_dir / "mcp_build" / "indico" / "entrypoint.py").is_file()
    assert config["mcp_servers"]["indico"]["build_context"] == "./mcp_build/indico"


def test_relative_external_context_resolved_against_config_path(tmp_path):
    base_dir = tmp_path / "archi-demo"
    base_dir.mkdir()
    src = _make_source(tmp_path, name="custom")
    # Config lives next to a sibling dir referenced relatively.
    config_path = src.parent / "config.yaml"

    config = {
        "_config_path": str(config_path),
        "mcp_servers": {"custom": {"build_context": "./custom"}},
    }
    ctx = _make_context(base_dir, config)

    _manager()._stage_mcp_copy(ctx)

    assert (base_dir / "mcp_build" / "custom" / "Dockerfile").is_file()
    assert config["mcp_servers"]["custom"]["build_context"] == "./mcp_build/custom"


def test_archi_code_context_is_left_untouched(tmp_path):
    base_dir = tmp_path / "archi-demo"
    base_dir.mkdir()

    config = {"mcp_servers": {"indico": {"build_context": "./archi_code/mcp/indico"}}}
    ctx = _make_context(base_dir, config)

    _manager()._stage_mcp_copy(ctx)

    # Shipped via copy_source_code; nothing copied, path preserved.
    assert not (base_dir / "mcp_build").exists()
    assert config["mcp_servers"]["indico"]["build_context"] == "./archi_code/mcp/indico"


def test_image_based_sidecar_is_untouched(tmp_path):
    base_dir = tmp_path / "archi-demo"
    base_dir.mkdir()

    config = {"mcp_servers": {"weather": {"image": "ghcr.io/example/weather:1.0"}}}
    ctx = _make_context(base_dir, config)

    _manager()._stage_mcp_copy(ctx)

    assert not (base_dir / "mcp_build").exists()
    assert "build_context" not in config["mcp_servers"]["weather"]


def test_missing_build_context_raises(tmp_path):
    base_dir = tmp_path / "archi-demo"
    base_dir.mkdir()

    config = {"mcp_servers": {"indico": {"build_context": str(tmp_path / "does-not-exist")}}}
    ctx = _make_context(base_dir, config)

    with pytest.raises(ValueError, match="build_context not found"):
        _manager()._stage_mcp_copy(ctx)


def test_no_mcp_servers_is_a_noop(tmp_path):
    base_dir = tmp_path / "archi-demo"
    base_dir.mkdir()
    ctx = _make_context(base_dir, {})

    _manager()._stage_mcp_copy(ctx)  # must not raise
    assert not (base_dir / "mcp_build").exists()


def _make_git_repo(tmp_path, name="upstream"):
    """A local git repo standing in for archi-physics/mcp-servers (no network)."""
    repo = tmp_path / name
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)

    def _git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    _git("config", "user.email", "t@example.com")
    _git("config", "user.name", "tester")
    (repo / "indico").mkdir()
    (repo / "indico" / "Dockerfile").write_text("FROM python:3.11-slim\n")
    (repo / "indico" / "entrypoint.py").write_text("print('hi')\n")
    (repo / "README.md").write_text("repo root\n")
    _git("add", "-A")
    _git("commit", "-m", "init")
    return repo


def test_git_build_context_is_cloned_and_rewritten(tmp_path):
    base_dir = tmp_path / "archi-demo"
    base_dir.mkdir()
    upstream = _make_git_repo(tmp_path)

    config = {
        "mcp_servers": {
            "indico": {"build_context": {"repo": str(upstream), "ref": "main", "subdir": "indico"}}
        }
    }
    ctx = _make_context(base_dir, config)

    _manager()._stage_mcp_copy(ctx)

    # subdir cloned into the deployment; .git excluded; path rewritten.
    assert (base_dir / "mcp_build" / "indico" / "Dockerfile").is_file()
    assert (base_dir / "mcp_build" / "indico" / "entrypoint.py").is_file()
    assert not (base_dir / "mcp_build" / "indico" / ".git").exists()
    assert config["mcp_servers"]["indico"]["build_context"] == "./mcp_build/indico"


def test_git_build_context_defaults_ref_to_main(tmp_path):
    base_dir = tmp_path / "archi-demo"
    base_dir.mkdir()
    upstream = _make_git_repo(tmp_path)

    # No ref -> defaults to main.
    config = {"mcp_servers": {"indico": {"build_context": {"repo": str(upstream), "subdir": "indico"}}}}
    ctx = _make_context(base_dir, config)

    _manager()._stage_mcp_copy(ctx)
    assert (base_dir / "mcp_build" / "indico" / "Dockerfile").is_file()


def test_git_build_context_missing_subdir_raises(tmp_path):
    base_dir = tmp_path / "archi-demo"
    base_dir.mkdir()
    upstream = _make_git_repo(tmp_path)

    config = {"mcp_servers": {"indico": {"build_context": {"repo": str(upstream), "subdir": "nope"}}}}
    ctx = _make_context(base_dir, config)

    with pytest.raises(ValueError, match="subdir 'nope' not found"):
        _manager()._stage_mcp_copy(ctx)


def test_git_build_context_missing_repo_raises(tmp_path):
    base_dir = tmp_path / "archi-demo"
    base_dir.mkdir()

    config = {"mcp_servers": {"indico": {"build_context": {"ref": "main", "subdir": "indico"}}}}
    ctx = _make_context(base_dir, config)

    with pytest.raises(ValueError, match="missing 'repo'"):
        _manager()._stage_mcp_copy(ctx)
