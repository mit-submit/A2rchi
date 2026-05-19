"""Path resolution for the benchmark harness.

Replaces hardcoded `/root/archi/...` paths scattered across
`src/bin/service_benchmark.py` and `scripts/*.py` with a single
env-overridable resolver. Downstream code calls `HarnessPaths.default()`
and navigates from there instead of string-concatenating.

Environment variables:

    ARCHI_REPO_ROOT   repo clone root. Default: parents[2] of this file.
    ARCHI_BENCH_OUT   bench output base. Default: <repo_root>/bench_out
    ARCHI_CONFIG_ROOT config dir. Default: <repo_root>/configs
    ARCHI_RUN_ID      optional run id override (otherwise auto-generated)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class HarnessPaths:
    repo_root: Path
    bench_out: Path
    config_root: Path
    run_id: str

    @classmethod
    def default(cls, run_id: Optional[str] = None) -> "HarnessPaths":
        repo_root = Path(os.environ.get("ARCHI_REPO_ROOT") or _default_repo_root())
        bench_out = Path(os.environ.get("ARCHI_BENCH_OUT") or (repo_root / "bench_out"))
        config_root = Path(os.environ.get("ARCHI_CONFIG_ROOT") or (repo_root / "configs"))
        chosen_run_id = run_id or os.environ.get("ARCHI_RUN_ID") or _utc_run_id()
        return cls(
            repo_root=repo_root,
            bench_out=bench_out,
            config_root=config_root,
            run_id=chosen_run_id,
        )

    @property
    def run_dir(self) -> Path:
        return self.bench_out / self.run_id

    @property
    def questions_dir(self) -> Path:
        return self.run_dir / "questions"

    @property
    def checkpoints_dir(self) -> Path:
        return self.run_dir / "checkpoints"

    @property
    def run_json(self) -> Path:
        return self.run_dir / "run.json"

    @property
    def run_metadata_json(self) -> Path:
        return self.run_dir / "metadata.json"

    def question_json(self, question_id: str) -> Path:
        return self.questions_dir / f"{question_id}.json"

    def config_for_host(self, host: str) -> Path:
        return self.config_root / host

    def ensure_run_dirs(self) -> None:
        """Create the run, questions, and checkpoints directories."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.questions_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoints_dir.mkdir(parents=True, exist_ok=True)
