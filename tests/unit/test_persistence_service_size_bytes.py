import sys
import types
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock

# Heavy-dep stubs live in tests/unit/conftest.py (guarded, shared).





from src.data_manager.collectors.persistence import PersistenceService


class _FakeResource:
    def __init__(self, resource_hash: str, filename: str, content: str):
        self._hash = resource_hash
        self._filename = filename
        self._content = content

    def get_hash(self) -> str:
        return self._hash

    def get_file_path(self, target_dir: Path) -> Path:
        return target_dir / self._filename

    def get_content(self):
        return self._content

    def get_metadata(self):
        # No size_bytes provided by resource metadata on purpose.
        return SimpleNamespace(as_dict=lambda: {"source_type": "ticket", "display_name": "Test Doc"})


def test_persist_resource_sets_size_bytes_from_written_file():
    with TemporaryDirectory() as tmp_dir:
        service = PersistenceService.__new__(PersistenceService)
        service.data_path = Path(tmp_dir)
        service.catalog = MagicMock()

        resource = _FakeResource("hash-1", "doc.txt", "hello persistence")
        target_dir = service.data_path / "tickets"
        persisted_path = service.persist_resource(resource, target_dir)

        assert persisted_path.exists()
        service.catalog.upsert_resource.assert_called_once()
        _, _, metadata = service.catalog.upsert_resource.call_args[0]
        assert metadata["size_bytes"] == str(len("hello persistence"))
