"""Atomic IO for per-question result files.

Writes each `QuestionResult` as `bench_out/<run_id>/questions/<qid>.json`
using a tmp+rename dance so a crash mid-write leaves either the old file
or the new file — never a half-written one.

Provides the scan used by `--retry-failed` mode: walk a run directory,
load all question results, and return the ones whose status is not
`ok` or whose answer is empty.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List

from src.harness.results_schema import QuestionResult


def write_question_result(path: Path, result: QuestionResult) -> None:
    """Atomic write of one QuestionResult to `path`.

    Writes to a sibling temp file then renames. Creates parent dirs if
    needed. If the write fails partway, the temp file is removed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # tempfile in the same dir so rename is atomic on POSIX
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(result.model_dump_json(indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_question_result(path: Path) -> QuestionResult:
    """Load a QuestionResult from disk. Raises pydantic.ValidationError on schema drift."""
    raw = json.loads(path.read_text())
    return QuestionResult.model_validate(raw)


def scan_run_questions(run_dir: Path) -> List[QuestionResult]:
    """Load every question result under `run_dir/questions/`. Sorted by question_id."""
    questions_dir = run_dir / "questions"
    if not questions_dir.is_dir():
        return []
    results: List[QuestionResult] = []
    for p in sorted(questions_dir.glob("*.json")):
        # Skip hidden temp files defensively
        if p.name.startswith("."):
            continue
        try:
            results.append(read_question_result(p))
        except Exception:
            # A corrupt or legacy file should not mask the rest of the run.
            # Treat it as "not present" — the caller's retry-failed logic
            # will not see this question_id and can re-run it.
            continue
    return results


def find_failed_question_ids(run_dir: Path) -> List[str]:
    """Question ids whose stored result is a failure, empty answer, or missing."""
    failed: List[str] = []
    for q in scan_run_questions(run_dir):
        if q.status != "ok":
            failed.append(q.question_id)
            continue
        if not q.answer or not q.answer.strip():
            failed.append(q.question_id)
    return failed


def find_successful_question_ids(run_dir: Path) -> List[str]:
    """Question ids whose stored result is a non-empty ok."""
    ok: List[str] = []
    for q in scan_run_questions(run_dir):
        if q.status == "ok" and q.answer and q.answer.strip():
            ok.append(q.question_id)
    return ok
