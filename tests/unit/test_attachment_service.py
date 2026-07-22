"""AttachmentService unit tests — psycopg2 fully mocked (repo pattern)."""
from unittest.mock import MagicMock, patch

import pytest

from src.utils.attachment_service import AttachmentService

PG = {"host": "x", "port": 5432, "database": "d", "user": "u", "password": "p"}


@pytest.fixture
def svc():
    return AttachmentService(connection_params=PG)


@pytest.fixture
def mock_db():
    with patch("src.utils.attachment_service.psycopg2.connect") as connect:
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor
        connect.return_value = conn
        yield connect, conn, cursor


def test_ensure_schema_creates_table_and_index(svc, mock_db):
    _, conn, cursor = mock_db
    svc.ensure_schema()
    executed = " ".join(str(c.args[0]) for c in cursor.execute.call_args_list)
    assert "CREATE TABLE IF NOT EXISTS conversation_attachments" in executed
    assert "ON DELETE CASCADE" in executed
    assert "idx_conv_attachments_conversation" in executed
    conn.commit.assert_called_once()


def test_verify_access_true_with_user(svc, mock_db):
    _, _, cursor = mock_db
    cursor.fetchone.return_value = (7, "t", None, None)
    assert svc.verify_conversation_access(7, "client-1", "user-1") is True
    sql = cursor.execute.call_args.args[0]
    assert "user_id = %s OR client_id = %s" in sql
    assert cursor.execute.call_args.args[1] == (7, "user-1", "client-1")


def test_verify_access_false_when_no_row(svc, mock_db):
    _, _, cursor = mock_db
    cursor.fetchone.return_value = None
    assert svc.verify_conversation_access(7, "client-1", None) is False
    sql = cursor.execute.call_args.args[0]
    assert "user_id = %s" not in sql
    assert cursor.execute.call_args.args[1] == (7, "client-1")


def test_create_attachment_returns_id(svc, mock_db):
    _, conn, cursor = mock_db
    cursor.fetchone.return_value = ("uuid-1", MagicMock(isoformat=lambda: "2026-07-06T00:00:00"))
    out = svc.create_attachment(
        conversation_id=7, owner="user-1", filename="a.txt", kind="document",
        size_bytes=5, extension=".txt", original_bytes=b"hello",
        extracted_text="hello", extraction_meta={"warnings": []},
    )
    assert out["attachment_id"] == "uuid-1"
    conn.commit.assert_called_once()
    sql = cursor.execute.call_args.args[0]
    assert "INSERT INTO conversation_attachments" in sql


def test_delete_is_ownership_joined(svc, mock_db):
    _, conn, cursor = mock_db
    cursor.fetchone.return_value = ("uuid-1",)
    assert svc.delete_attachment("uuid-1", "client-1", "user-1") is True
    sql = cursor.execute.call_args.args[0]
    assert "USING conversation_metadata" in sql
    assert "RETURNING" in sql
    conn.commit.assert_called_once()
    assert cursor.execute.call_args.args[1] == ("uuid-1", "user-1", "client-1")


def test_delete_returns_false_when_not_owner(svc, mock_db):
    _, _, cursor = mock_db
    cursor.fetchone.return_value = None
    assert svc.delete_attachment("uuid-1", "client-2", None) is False
    sql = cursor.execute.call_args.args[0]
    assert "user_id = %s" not in sql
    assert cursor.execute.call_args.args[1] == ("uuid-1", "client-2")


def test_bind_unbound_to_message(svc, mock_db):
    _, conn, cursor = mock_db
    svc.bind_unbound_to_message(7, 123)
    sql = cursor.execute.call_args.args[0]
    assert "SET message_id = %s" in sql and "message_id IS NULL" in sql
    conn.commit.assert_called_once()


def test_list_excludes_bytes(svc, mock_db):
    _, _, cursor = mock_db
    cursor.fetchall.return_value = []
    svc.list_for_conversation(7)
    sql = cursor.execute.call_args.args[0]
    assert "original_bytes" not in sql and "extracted_text" not in sql


def test_create_strips_nuls_from_client_controlled_fields(svc, mock_db):
    _, _, cursor = mock_db
    cursor.fetchone.return_value = ("uuid-1", MagicMock(isoformat=lambda: "t"))
    svc.create_attachment(
        conversation_id=7, owner="u\x00ser", filename="evil\x00.txt", kind="document",
        size_bytes=3, extension=".txt", original_bytes=b"abc",
        extracted_text="a\x00b", extraction_meta={"warnings": ["bad\x00text"]},
    )
    params = cursor.execute.call_args.args[1]
    assert params[1] == "user"                     # owner sanitized
    assert params[2] == "evil.txt"                 # filename sanitized
    assert params[7] == "ab"                       # text sanitized
    assert params[8].adapted == {"warnings": ["badtext"]}   # Json payload sanitized


def test_get_context_items_shape_and_order(svc, mock_db):
    import datetime
    _, _, cursor = mock_db
    ts = datetime.datetime(2026, 7, 6, 12, 0, 0)
    cursor.fetchall.return_value = [
        ("a.txt", "document", "alpha", {"warnings": []}, ts),
    ]
    items = svc.get_context_items(7)
    sql = cursor.execute.call_args.args[0]
    assert "ORDER BY created_at ASC, attachment_id ASC" in sql
    assert items == [{
        "filename": "a.txt", "kind": "document", "extracted_text": "alpha",
        "extraction_meta": {"warnings": []}, "created_at": ts,
    }]


def test_count_for_conversation(svc, mock_db):
    _, _, cursor = mock_db
    cursor.fetchone.return_value = (3,)
    assert svc.count_for_conversation(7) == 3
    assert "COUNT(*)" in cursor.execute.call_args.args[0]


def test_list_row_shape_isoformats_created_at(svc, mock_db):
    import datetime, json
    _, _, cursor = mock_db
    ts = datetime.datetime(2026, 7, 6, 12, 0, 0)
    cursor.fetchall.return_value = [
        ("uuid-1", "a.txt", "document", 5, ".txt", None, ts, json.dumps({"warnings": []})),
    ]
    rows = svc.list_for_conversation(7)
    assert rows[0]["created_at"] == ts.isoformat()
    assert rows[0]["extraction_meta"] == {"warnings": []}   # JSON string decoded
    assert rows[0]["message_id"] is None


def test_sql_title_constants_exist():
    from src.utils.sql import SQL_UPDATE_CONVERSATION_TITLE, SQL_UPDATE_CONVERSATION_TITLE_BY_USER
    assert "SET title" in SQL_UPDATE_CONVERSATION_TITLE
    assert "user_id = %s OR client_id = %s" in SQL_UPDATE_CONVERSATION_TITLE_BY_USER


def test_get_for_tools_returns_row_and_bytes(svc, mock_db):
    _, _, cursor = mock_db
    cursor.fetchone.return_value = ("a.zip", "bundle", "text", {"entries": []}, memoryview(b"PK"))
    row = svc.get_for_tools(7, "a.zip")
    assert row["original_bytes"] == b"PK" and isinstance(row["original_bytes"], bytes)
    assert row["kind"] == "bundle"
    sql, params = cursor.execute.call_args[0]
    assert params == (7, "a.zip")
    assert "conversation_id = %s" in sql and "filename = %s" in sql
    assert "ORDER BY" in sql and "DESC" in sql          # newest duplicate wins


def test_get_for_tools_missing_returns_none(svc, mock_db):
    _, _, cursor = mock_db
    cursor.fetchone.return_value = None
    assert svc.get_for_tools(7, "ghost.txt") is None


def test_bytes_for_owner_sums_size(svc, mock_db):
    _, _, cursor = mock_db
    cursor.fetchone.return_value = (123456,)
    assert svc.bytes_for_owner("user-1") == 123456
    sql = cursor.execute.call_args.args[0]
    assert "SUM(size_bytes)" in sql and "owner = %s" in sql
    assert cursor.execute.call_args.args[1] == ("user-1",)


def test_bytes_for_owner_handles_null(svc, mock_db):
    _, _, cursor = mock_db
    cursor.fetchone.return_value = (None,)
    assert svc.bytes_for_owner("nobody") == 0


def test_sweep_abandoned_conversations_deletes_and_returns_count(svc, mock_db):
    _, conn, cursor = mock_db
    cursor.rowcount = 4
    assert svc.sweep_abandoned_conversations(72) == 4
    sql = cursor.execute.call_args.args[0]
    assert "DELETE FROM conversation_metadata" in sql
    assert "NOT EXISTS" in sql and "FROM conversations" in sql          # message-less
    assert "EXISTS" in sql and "conversation_attachments" in sql        # has an attachment
    assert cursor.execute.call_args.args[1] == (72,)
    conn.commit.assert_called_once()


def test_sweep_abandoned_conversations_disabled_is_noop(svc, mock_db):
    connect, _, cursor = mock_db
    assert svc.sweep_abandoned_conversations(0) == 0
    connect.assert_not_called()          # ttl<=0 must not even open a connection
    cursor.execute.assert_not_called()
