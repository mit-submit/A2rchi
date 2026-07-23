import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Heavy-dep stubs live in tests/unit/conftest.py (guarded, shared).










from src.data_manager.vectorstore import manager as manager_module
from src.data_manager.vectorstore.manager import VectorStoreManager


class _InlineFuture:
    def __init__(self, fn, *args, **kwargs):
        self._exc = None
        self._result = None
        try:
            self._result = fn(*args, **kwargs)
        except Exception as exc:  # pragma: no cover - defensive
            self._exc = exc

    def result(self):
        if self._exc:
            raise self._exc
        return self._result


class _InlineExecutor:
    def __init__(self, max_workers=1):
        self.max_workers = max_workers
        self.futures = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def submit(self, fn, *args, **kwargs):
        fut = _InlineFuture(fn, *args, **kwargs)
        self.futures.append(fut)
        return fut


def test_add_to_postgres_commits_every_25_files(monkeypatch):
    manager = VectorStoreManager.__new__(VectorStoreManager)
    manager.parallel_workers = 1
    manager.collection_name = "test_collection"
    manager._data_manager_config = {"stemming": {"enabled": False}}
    manager._pg_config = {"host": "localhost"}

    catalog = MagicMock()
    catalog.get_document_id.return_value = 1
    catalog.get_metadata_for_hash.return_value = {}
    manager._catalog = catalog

    split_doc = SimpleNamespace(page_content="hello world", metadata={})
    manager.text_splitter = SimpleNamespace(split_documents=lambda docs: [split_doc])
    manager.embedding_model = SimpleNamespace(embed_documents=lambda chunks: [[0.1, 0.2, 0.3] for _ in chunks])
    manager.loader = lambda _path: SimpleNamespace(load=lambda: [split_doc])

    fake_cursor = MagicMock()
    fake_conn = MagicMock()
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor
    fake_conn.cursor.return_value.__exit__.return_value = False

    monkeypatch.setattr(manager_module.psycopg2, "connect", lambda **_kwargs: fake_conn)
    monkeypatch.setattr(manager_module.psycopg2.extras, "execute_values", lambda *args, **kwargs: None)
    monkeypatch.setattr(manager_module, "ThreadPoolExecutor", _InlineExecutor)
    monkeypatch.setattr(manager_module, "as_completed", lambda futures: list(futures))

    files_to_add = {f"hash-{i}": f"/tmp/file-{i}.txt" for i in range(26)}
    manager._add_to_postgres(files_to_add)

    # First commit at 25 files, second for final remainder.
    assert fake_conn.commit.call_count == 2
    # All documents are marked embedding at start of run.
    assert catalog.update_ingestion_status.call_count >= 26
