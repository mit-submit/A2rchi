# isort: skip_file
import json

import pytest

from src.evaluation.qa.artifacts import AtomicJsonlWriter


class TestAtomicJsonlWriter:
    def test_replaces_complete_artifact(self, tmp_path):
        path = tmp_path / "rows.jsonl"

        with AtomicJsonlWriter(path) as writer:
            writer.write({"id": 1})
            writer.write({"id": 2})

        assert [json.loads(line) for line in path.read_text().splitlines()] == [
            {"id": 1},
            {"id": 2},
        ]

    def test_failure_preserves_previous_artifact(self, tmp_path):
        path = tmp_path / "rows.jsonl"
        path.write_text('{"old": true}\n')

        with pytest.raises(RuntimeError, match="stop"):
            with AtomicJsonlWriter(path) as writer:
                writer.write({"new": True})
                raise RuntimeError("stop")

        assert path.read_text() == '{"old": true}\n'
