"""Unit tests for PlaybookService — mocked psycopg2, no real DB required."""

import pytest

from src.utils import sql
from src.utils.playbook_service import (
    MAX_ENABLED_PUBLIC_PER_USER,
    PlaybookNotFoundError,
    PlaybookService,
    PlaybookValidationError,
)


# ---------------------------------------------------------------------------
# Minimal fake helpers (no real DB)
# ---------------------------------------------------------------------------

class _FakeCursor:
    """A cursor stub that pops from a pre-loaded fetchone_values list and counts INSERTs."""

    def __init__(self, fetchone_values=None):
        self._fetchone_values = list(fetchone_values or [])
        self.executed_inserts = 0

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith("INSERT"):
            self.executed_inserts += 1

    def fetchone(self):
        if self._fetchone_values:
            return self._fetchone_values.pop(0)
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class _FakeConn:
    """A connection stub that returns a single shared _FakeCursor and records commits/rollbacks."""

    def __init__(self, cursor):
        self._cursor = cursor
        self.rollback_called = False

    def cursor(self, **kwargs):
        return self._cursor

    def commit(self):
        pass

    def rollback(self):
        self.rollback_called = True


# ---------------------------------------------------------------------------
# Visibility guard
# ---------------------------------------------------------------------------

def test_enable_playbook_rejects_foreign_private(monkeypatch):
    svc = PlaybookService(pg_config={"dummy": True})
    fake_cursor = _FakeCursor(fetchone_values=[("private", "owner-x")])  # visibility, owner
    monkeypatch.setattr(svc, "_get_connection", lambda: _FakeConn(fake_cursor))
    monkeypatch.setattr(svc, "_release_connection", lambda conn: None)
    with pytest.raises(PlaybookValidationError):
        svc.enable_playbook("user-B", 42)
    assert fake_cursor.executed_inserts == 0  # never reached the INSERT


def test_enable_playbook_rolls_back_on_foreign_private(monkeypatch):
    """Connection must be rolled back before release on the foreign-private guard path."""
    svc = PlaybookService(pg_config={"dummy": True})
    fake_cursor = _FakeCursor(fetchone_values=[("private", "owner-x")])
    fake_conn = _FakeConn(fake_cursor)
    monkeypatch.setattr(svc, "_get_connection", lambda: fake_conn)
    monkeypatch.setattr(svc, "_release_connection", lambda conn: None)
    with pytest.raises(PlaybookValidationError):
        svc.enable_playbook("user-B", 42)
    assert fake_conn.rollback_called, "rollback() must be called before releasing on a guard raise"
    assert fake_cursor.executed_inserts == 0


def test_enable_playbook_allows_public(monkeypatch):
    """A public playbook owned by someone else should reach the INSERT without raising."""
    svc = PlaybookService(pg_config={"dummy": True})
    # First fetchone: (visibility, owner_id); second fetchone: COUNT result
    fake_cursor = _FakeCursor(fetchone_values=[("public", "owner-x"), (0,)])
    fake_conn = _FakeConn(fake_cursor)
    monkeypatch.setattr(svc, "_get_connection", lambda: fake_conn)
    monkeypatch.setattr(svc, "_release_connection", lambda conn: None)
    svc.enable_playbook("user-B", 42)  # should not raise
    assert fake_cursor.executed_inserts == 1
    assert not fake_conn.rollback_called


def test_enable_playbook_enforces_cap(monkeypatch):
    """When the user is at the cap, enable_playbook raises and does NOT INSERT."""
    svc = PlaybookService(pg_config={"dummy": True})
    fake_cursor = _FakeCursor(
        fetchone_values=[("public", "owner-x"), (MAX_ENABLED_PUBLIC_PER_USER,)]
    )
    fake_conn = _FakeConn(fake_cursor)
    monkeypatch.setattr(svc, "_get_connection", lambda: fake_conn)
    monkeypatch.setattr(svc, "_release_connection", lambda conn: None)
    with pytest.raises(PlaybookValidationError, match="limit reached"):
        svc.enable_playbook("user-B", 42)
    assert fake_cursor.executed_inserts == 0
    assert fake_conn.rollback_called, "rollback() must be called on the cap-exceeded path"


# ---------------------------------------------------------------------------
# Per-turn side-table helpers (moved here from the chat-app wrapper)
# ---------------------------------------------------------------------------

class _RecordingCursor(_FakeCursor):
    """_FakeCursor that also records every (sql, params) pair executed."""

    def __init__(self, fetchone_values=None):
        super().__init__(fetchone_values)
        self.executed = []

    def execute(self, statement, params=None):
        self.executed.append((statement, params))
        super().execute(statement, params)


def test_last_playbook_name_for_sender_returns_stored_name(monkeypatch):
    svc = PlaybookService(pg_config={"dummy": True})
    cursor = _RecordingCursor(fetchone_values=[("transfer-check",)])
    monkeypatch.setattr(svc, "_get_connection", lambda: _FakeConn(cursor))
    monkeypatch.setattr(svc, "_release_connection", lambda conn: None)
    assert svc.last_playbook_name_for_sender(7, "user") == "transfer-check"
    statement, params = cursor.executed[0]
    assert statement == sql.SQL_LAST_PLAYBOOK_NAME_FOR_SENDER
    assert params == (7, "user")


def test_last_playbook_name_for_sender_none_for_plain_newest_turn(monkeypatch):
    """LEFT JOIN semantics: a newest sender turn without a playbook yields (None,) —
    the method must return None, not splice in an older turn's playbook."""
    svc = PlaybookService(pg_config={"dummy": True})
    cursor = _RecordingCursor(fetchone_values=[(None,)])
    monkeypatch.setattr(svc, "_get_connection", lambda: _FakeConn(cursor))
    monkeypatch.setattr(svc, "_release_connection", lambda conn: None)
    assert svc.last_playbook_name_for_sender(7, "user") is None


def test_last_playbook_name_for_sender_none_when_no_rows(monkeypatch):
    svc = PlaybookService(pg_config={"dummy": True})
    cursor = _RecordingCursor(fetchone_values=[])
    monkeypatch.setattr(svc, "_get_connection", lambda: _FakeConn(cursor))
    monkeypatch.setattr(svc, "_release_connection", lambda conn: None)
    assert svc.last_playbook_name_for_sender(7, "user") is None


def test_last_playbook_name_for_sender_raises_on_db_error(monkeypatch):
    """The service reports DB errors; best-effort policy lives at the chat-app call site."""
    svc = PlaybookService(pg_config={"dummy": True})

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "_get_connection", boom)
    with pytest.raises(RuntimeError, match="db down"):
        svc.last_playbook_name_for_sender(7, "user")


class _RecordingConn(_FakeConn):
    """_FakeConn that also records whether commit() was called."""

    def __init__(self, cursor):
        super().__init__(cursor)
        self.committed = False

    def commit(self):
        self.committed = True


def test_record_playbook_turn_executes_upsert(monkeypatch):
    svc = PlaybookService(pg_config={"dummy": True})
    cursor = _RecordingCursor()
    conn = _RecordingConn(cursor)
    monkeypatch.setattr(svc, "_get_connection", lambda: conn)
    monkeypatch.setattr(svc, "_release_connection", lambda c: None)
    svc.record_playbook_turn(11, "rucio-triage", 3)
    statement, params = cursor.executed[0]
    assert statement == sql.SQL_INSERT_PLAYBOOK_TURN
    assert params == (11, "rucio-triage", 3)
    assert conn.committed


def test_record_playbook_turn_skips_without_message_id_or_name(monkeypatch):
    """Falsy message_id or playbook_name is a no-op that never touches the DB."""
    svc = PlaybookService(pg_config={"dummy": True})

    def no_connect():
        raise AssertionError("record_playbook_turn must not connect for a no-op")

    monkeypatch.setattr(svc, "_get_connection", no_connect)
    svc.record_playbook_turn(None, "rucio-triage")
    svc.record_playbook_turn(11, None)
    svc.record_playbook_turn(0, "")


def test_record_playbook_turn_raises_on_db_error(monkeypatch):
    """The service reports DB errors; the chat flow decides that a missing side
    table must not break the conversation insert."""
    svc = PlaybookService(pg_config={"dummy": True})

    def boom():
        raise RuntimeError("side table missing")

    monkeypatch.setattr(svc, "_get_connection", boom)
    with pytest.raises(RuntimeError, match="side table missing"):
        svc.record_playbook_turn(11, "rucio-triage")


# ---------------------------------------------------------------------------
# Unified invocation ledger (playbook_invocations)
# ---------------------------------------------------------------------------

def test_record_invocation_executes_insert(monkeypatch):
    """An explicit /name use lands one row: params carry conversation_id,
    message_id, playbook_id, name, source and status in column order; arm NULL."""
    svc = PlaybookService(pg_config={"dummy": True})
    cursor = _RecordingCursor()
    conn = _RecordingConn(cursor)
    monkeypatch.setattr(svc, "_get_connection", lambda: conn)
    monkeypatch.setattr(svc, "_release_connection", lambda c: None)
    svc.record_invocation(5, 11, 3, "rucio-triage", "explicit", "ok")
    statement, params = cursor.executed[0]
    assert statement == sql.SQL_INSERT_PLAYBOOK_INVOCATION
    assert params == (5, 11, 3, "rucio-triage", "explicit", "ok", None)
    assert conn.committed


def test_record_invocation_defaults_status_and_carries_arm(monkeypatch):
    """status defaults to 'ok'; an A/B-arm auto row carries its arm label."""
    svc = PlaybookService(pg_config={"dummy": True})
    cursor = _RecordingCursor()
    conn = _RecordingConn(cursor)
    monkeypatch.setattr(svc, "_get_connection", lambda: conn)
    monkeypatch.setattr(svc, "_release_connection", lambda c: None)
    svc.record_invocation(9, 21, None, "auto-pb", "auto", arm="b")
    _, params = cursor.executed[0]
    assert params == (9, 21, None, "auto-pb", "auto", "ok", "b")


def test_record_invocation_failed_explicit_writes_null_ids(monkeypatch):
    """A failed /name (playbook not found) has no conversation yet — the row
    carries NULL conversation_id/message_id/playbook_id but the attempted name."""
    svc = PlaybookService(pg_config={"dummy": True})
    cursor = _RecordingCursor()
    conn = _RecordingConn(cursor)
    monkeypatch.setattr(svc, "_get_connection", lambda: conn)
    monkeypatch.setattr(svc, "_release_connection", lambda c: None)
    svc.record_invocation(None, None, None, "ghost-pb", "explicit", "not_found")
    _, params = cursor.executed[0]
    assert params == (None, None, None, "ghost-pb", "explicit", "not_found", None)


def test_record_invocation_skips_without_name(monkeypatch):
    """A falsy playbook_name is a no-op (the column is NOT NULL) — never connects."""
    svc = PlaybookService(pg_config={"dummy": True})

    def no_connect():
        raise AssertionError("record_invocation must not connect for a no-op")

    monkeypatch.setattr(svc, "_get_connection", no_connect)
    svc.record_invocation(1, 2, 3, "", "auto")
    svc.record_invocation(1, 2, 3, None, "explicit")


def test_record_invocation_raises_on_db_error(monkeypatch):
    """The service reports DB errors; callers wrap it best-effort so a missing
    ledger table never breaks a turn (mirrors record_playbook_turn)."""
    svc = PlaybookService(pg_config={"dummy": True})

    def boom():
        raise RuntimeError("ledger missing")

    monkeypatch.setattr(svc, "_get_connection", boom)
    with pytest.raises(RuntimeError, match="ledger missing"):
        svc.record_invocation(1, 2, 3, "rucio-triage", "explicit")


# ---------------------------------------------------------------------------
# ensure_schema (moved here from the chat-app wrapper's _ensure_playbook_schema)
# ---------------------------------------------------------------------------

def _run_ensure_schema(monkeypatch, fetchone_values):
    svc = PlaybookService(pg_config={"dummy": True})
    cursor = _RecordingCursor(fetchone_values=fetchone_values)
    conn = _RecordingConn(cursor)
    monkeypatch.setattr(svc, "_get_connection", lambda: conn)
    monkeypatch.setattr(svc, "_release_connection", lambda c: None)
    svc.ensure_schema()
    return cursor, conn


def test_ensure_schema_creates_playbook_schema_idempotently(monkeypatch):
    """Both tables + the opt-in table are created, every DDL is IF NOT EXISTS
    (safe to re-run on every service start), and the work is committed."""
    cursor, conn = _run_ensure_schema(monkeypatch, fetchone_values=[None])
    blob = "\n".join(statement for statement, _ in cursor.executed)
    assert "CREATE TABLE IF NOT EXISTS playbooks" in blob
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_playbooks_owner_name" in blob
    assert "ADD COLUMN IF NOT EXISTS visibility" in blob
    assert "CREATE TABLE IF NOT EXISTS conversation_playbook_turns" in blob
    assert "CREATE TABLE IF NOT EXISTS user_enabled_playbooks" in blob
    for statement, _ in cursor.executed:
        if statement.strip().upper().startswith(("CREATE TABLE", "CREATE INDEX", "CREATE UNIQUE INDEX")):
            assert "IF NOT EXISTS" in statement, f"non-idempotent DDL: {statement[:60]}"
    assert conn.committed


def test_ensure_schema_creates_playbook_invocations_table(monkeypatch):
    """The unified ledger table + its (playbook_name, ts) index are created, and
    the ledger never carries an owner_id column (owner ids double as credentials)."""
    cursor, _ = _run_ensure_schema(monkeypatch, fetchone_values=[None])
    blob = "\n".join(statement for statement, _ in cursor.executed)
    assert "CREATE TABLE IF NOT EXISTS playbook_invocations" in blob
    assert "idx_playbook_invocations" in blob
    inv_ddl = [s for s, _ in cursor.executed if "playbook_invocations" in s]
    assert inv_ddl, "no playbook_invocations DDL was issued"
    assert all("owner_id" not in s for s in inv_ddl), "ledger must have no owner_id column"


def test_ensure_schema_skips_legacy_copy_without_column(monkeypatch):
    """No legacy conversations.playbook_name column -> no copy INSERT is issued."""
    cursor, _ = _run_ensure_schema(monkeypatch, fetchone_values=[None])
    assert not any(
        "INSERT INTO conversation_playbook_turns" in statement for statement, _ in cursor.executed
    )


def test_ensure_schema_copies_legacy_column_when_present(monkeypatch):
    """A legacy column is migrated once, guarded by information_schema, idempotently."""
    cursor, _ = _run_ensure_schema(monkeypatch, fetchone_values=[(1,)])
    copies = [s for s, _ in cursor.executed if "INSERT INTO conversation_playbook_turns" in s]
    assert len(copies) == 1
    assert "FROM conversations" in copies[0]
    assert "ON CONFLICT (message_id) DO NOTHING" in copies[0]


def test_ensure_schema_raises_on_db_error(monkeypatch):
    """ensure_schema reports failures; the entrypoint decides that a failed
    migration must not block service startup."""
    svc = PlaybookService(pg_config={"dummy": True})

    def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(svc, "_get_connection", boom)
    with pytest.raises(RuntimeError, match="no database"):
        svc.ensure_schema()
