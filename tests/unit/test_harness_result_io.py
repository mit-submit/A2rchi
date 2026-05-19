"""Unit tests for per-question result IO and failed-question scanning."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.harness.result_io import (
    find_failed_question_ids,
    find_successful_question_ids,
    read_question_result,
    scan_run_questions,
    write_question_result,
)
from src.harness.results_schema import FailureRecord, QuestionResult, TokenUsage


def _q(
    question_id: str,
    *,
    answer: str | None = "answer",
    status: str = "ok",
) -> QuestionResult:
    return QuestionResult(
        question_id=question_id,
        question=f"question-{question_id}",
        answer=answer,
        status=status,
        token_usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )


def test_write_and_read_roundtrip(tmp_path: Path):
    p = tmp_path / "run" / "questions" / "q1.json"
    q = _q("q1")
    write_question_result(p, q)
    assert p.exists()
    loaded = read_question_result(p)
    assert loaded.question_id == "q1"
    assert loaded.status == "ok"
    assert loaded.token_usage.total_tokens == 2


def test_write_is_atomic_no_tmp_left_behind(tmp_path: Path):
    p = tmp_path / "run" / "questions" / "q1.json"
    write_question_result(p, _q("q1"))
    # No leftover .tmp files
    leftovers = [f for f in p.parent.iterdir() if f.name.startswith(".")]
    assert leftovers == []


def test_write_cleans_up_tmp_on_failure(tmp_path: Path, monkeypatch):
    p = tmp_path / "run" / "questions" / "q1.json"
    # Patch os.replace to fail so the write aborts mid-way
    real_replace = os.replace

    def _boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr("src.harness.result_io.os.replace", _boom)
    with pytest.raises(OSError):
        write_question_result(p, _q("q1"))

    # The destination file should NOT exist, and no .tmp file should remain
    assert not p.exists()
    leftovers = [f for f in p.parent.iterdir() if f.name.startswith(".")]
    assert leftovers == []


def test_scan_run_questions_empty_dir(tmp_path: Path):
    assert scan_run_questions(tmp_path / "does-not-exist") == []
    (tmp_path / "questions").mkdir()
    assert scan_run_questions(tmp_path) == []


def test_scan_run_questions_sorted(tmp_path: Path):
    for qid in ["q3", "q1", "q2"]:
        write_question_result(tmp_path / "questions" / f"{qid}.json", _q(qid))
    results = scan_run_questions(tmp_path)
    assert [q.question_id for q in results] == ["q1", "q2", "q3"]


def test_scan_skips_corrupt_files(tmp_path: Path):
    write_question_result(tmp_path / "questions" / "q1.json", _q("q1"))
    (tmp_path / "questions" / "q2.json").write_text("not valid json { {")
    results = scan_run_questions(tmp_path)
    # Only q1 comes back; corrupt q2 is silently skipped so --retry-failed will re-run it.
    assert [q.question_id for q in results] == ["q1"]


def test_find_failed_question_ids_catches_failed_empty_and_blank(tmp_path: Path):
    write_question_result(tmp_path / "questions" / "ok.json", _q("ok"))
    write_question_result(
        tmp_path / "questions" / "failed.json",
        _q("failed", answer=None, status="failed"),
    )
    write_question_result(
        tmp_path / "questions" / "empty.json",
        _q("empty", answer="", status="ok"),  # status ok but blank answer
    )
    write_question_result(
        tmp_path / "questions" / "timeout.json",
        _q("timeout", answer=None, status="timeout"),
    )
    write_question_result(
        tmp_path / "questions" / "whitespace.json",
        _q("whitespace", answer="   ", status="ok"),
    )

    failed = find_failed_question_ids(tmp_path)
    assert set(failed) == {"failed", "empty", "timeout", "whitespace"}
    assert "ok" not in failed


def test_find_successful_question_ids(tmp_path: Path):
    write_question_result(tmp_path / "questions" / "ok.json", _q("ok"))
    write_question_result(
        tmp_path / "questions" / "failed.json",
        _q("failed", status="failed"),
    )
    write_question_result(
        tmp_path / "questions" / "empty_ok.json",
        _q("empty_ok", answer="", status="ok"),
    )
    ok = find_successful_question_ids(tmp_path)
    assert ok == ["ok"]
