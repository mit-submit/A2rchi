import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_import_probe(module_name: str) -> str:
    probe = f"""
import importlib
import sys

importlib.import_module({module_name!r})
print("base_react_loaded=" + str("src.archi.pipelines.agents.base_react" in sys.modules))
print("cms_comp_ops_loaded=" + str("src.archi.pipelines.agents.cms_comp_ops_agent" in sys.modules))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_chat_app_import_does_not_load_react_runtime_modules():
    stdout = _run_import_probe("src.interfaces.chat_app.app")

    assert "base_react_loaded=False" in stdout
    assert "cms_comp_ops_loaded=False" in stdout


def test_ab_agent_spec_service_import_does_not_load_react_runtime_modules():
    stdout = _run_import_probe("src.utils.ab_agent_spec_service")

    assert "base_react_loaded=False" in stdout
    assert "cms_comp_ops_loaded=False" in stdout


def test_agent_spec_import_does_not_load_react_runtime_modules():
    stdout = _run_import_probe("src.archi.pipelines.agents.agent_spec")

    assert "base_react_loaded=False" in stdout
    assert "cms_comp_ops_loaded=False" in stdout
