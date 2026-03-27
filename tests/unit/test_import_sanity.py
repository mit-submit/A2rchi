"""Import sanity test — regression for bug #8 (circular import).

Verifies that the core modules can be imported without circular
dependency errors in a clean Python process.
"""

import subprocess
import sys

import pytest


class TestNoCircularImports:
    """Regression for bug #8: circular import between modules."""

    @pytest.mark.parametrize(
        "module",
        [
            "src.archi.archi",
            "src.archi.copilot_event_adapter",
            "src.archi.utils.output_dataclass",
            "src.archi.pipelines.agents.tools.local_files",
        ],
    )
    def test_module_imports_cleanly(self, module):
        """Each module must be importable without ImportError in a clean process."""
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, (
            f"Failed to import {module}:\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
