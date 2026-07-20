"""Route tests with a minimal Flask app and a mocked service (no DB, no auth)."""
import io
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from flask import Flask

from src.interfaces.chat_app.attachment_routes import register_attachments

CFG = {
    "enabled": True,
    "max_file_mb": 20,
    "max_per_conversation": 10,
    "text_budget_chars": 400000,
    "text_poor_page_chars": 200,
    "zip_max_decompressed_mb": 100,
    "zip_max_entries": 200,
}


@pytest.fixture
def service():
    svc = MagicMock()
    svc.verify_conversation_access.return_value = True
    svc.count_for_conversation.return_value = 0
    svc.create_attachment.return_value = {
        "attachment_id": "uuid-1", "created_at": "2026-07-06T00:00:00",
    }
    svc.list_for_conversation.return_value = []
    svc.delete_attachment.return_value = True
    svc.bytes_for_owner.return_value = 0
    return svc


@pytest.fixture
def client(service):
    app = Flask(__name__)
    app.secret_key = "test"
    create_conv = MagicMock(return_value=42)
    register_attachments(
        app,
        service=service,
        perm_decorator=lambda f: f,          # auth pass-through in unit tests
        create_conversation_fn=create_conv,
        attachments_config=CFG,
    )
    app.config["TESTING"] = True
    c = app.test_client()
    c._create_conv = create_conv
    return c


def _post_file(client, name="notes.txt", content=b"hello", extra=None):
    data = {"client_id": "client-1", "file": (io.BytesIO(content), name)}
    data.update(extra or {})
    return client.post(
        "/api/chat/attachments", data=data, content_type="multipart/form-data"
    )


def test_attach_without_conversation_creates_one(client):
    resp = _post_file(client)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["conversation_id"] == 42
    assert body["attachment_id"] == "uuid-1"
    client._create_conv.assert_called_once_with("New conversation", "client-1", None)


def test_attach_to_existing_conversation_checks_ownership(client, service):
    resp = _post_file(client, extra={"conversation_id": "7"})
    assert resp.status_code == 201
    service.verify_conversation_access.assert_called_once_with(7, "client-1", None)
    client._create_conv.assert_not_called()


def test_attach_foreign_conversation_404(client, service):
    service.verify_conversation_access.return_value = False
    resp = _post_file(client, extra={"conversation_id": "9"})
    assert resp.status_code == 404


def test_missing_file_400(client):
    resp = client.post(
        "/api/chat/attachments", data={"client_id": "c"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_missing_client_id_400(client):
    resp = client.post(
        "/api/chat/attachments",
        data={"file": (io.BytesIO(b"x"), "a.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_count_cap_409(client, service):
    service.count_for_conversation.return_value = 10
    resp = _post_file(client, extra={"conversation_id": "7"})
    assert resp.status_code == 409


def test_unsupported_type_415_with_message(client):
    resp = _post_file(client, name="cat.png")
    assert resp.status_code == 415
    assert "Images aren't supported yet" in resp.get_json()["error"]


def test_rejected_attach_creates_no_conversation(client):
    resp = _post_file(client, name="cat.png")   # no conversation_id supplied
    assert resp.status_code == 415
    client._create_conv.assert_not_called()


def test_list_returns_metadata(client, service):
    service.list_for_conversation.return_value = [{"attachment_id": "uuid-1"}]
    resp = client.get("/api/chat/conversations/7/attachments?client_id=client-1")
    assert resp.status_code == 200
    assert resp.get_json()["attachments"] == [{"attachment_id": "uuid-1"}]


def test_list_foreign_conversation_404(client, service):
    service.verify_conversation_access.return_value = False
    resp = client.get("/api/chat/conversations/7/attachments?client_id=other")
    assert resp.status_code == 404


def test_delete_204(client, service):
    resp = client.delete("/api/chat/attachments/2d0d7f2e-0000-4000-8000-000000000000?client_id=client-1")
    assert resp.status_code == 204


def test_delete_not_owner_404(client, service):
    service.delete_attachment.return_value = False
    resp = client.delete("/api/chat/attachments/2d0d7f2e-0000-4000-8000-000000000000?client_id=x")
    assert resp.status_code == 404


def test_delete_invalid_uuid_404(client):
    resp = client.delete("/api/chat/attachments/not-a-uuid?client_id=c")
    assert resp.status_code == 404


def test_client_id_nul_bytes_sanitized(client, service):
    # _post_file's `data.update(extra or {})` overwrites the default
    # client_id key (dict.update replaces existing keys), so passing
    # client_id in extra correctly replaces "client-1" — no need to
    # build a separate data dict here.
    resp = _post_file(client, extra={"conversation_id": "7", "client_id": "cli\x00ent-1"})
    assert resp.status_code == 201
    service.verify_conversation_access.assert_called_once_with(7, "client-1", None)


def test_list_client_id_nul_sanitized(client, service):
    resp = client.get(
        "/api/chat/conversations/7/attachments",
        query_string={"client_id": "cli\x00ent-1"},
    )
    assert resp.status_code == 200
    service.verify_conversation_access.assert_called_once_with(7, "client-1", None)


def test_delete_client_id_nul_sanitized(client, service):
    resp = client.delete(
        "/api/chat/attachments/2d0d7f2e-0000-4000-8000-000000000000",
        query_string={"client_id": "cli\x00ent-1"},
    )
    assert resp.status_code == 204
    service.delete_attachment.assert_called_once_with(
        "2d0d7f2e-0000-4000-8000-000000000000", "client-1", None
    )


# --- Per-user storage quota (A3) --------------------------------------------

def test_over_quota_413_and_stores_nothing(client, service):
    # CFG has no max_total_mb_per_user -> default 512 MB; pin usage at the cap
    # so any further byte overflows it.
    service.bytes_for_owner.return_value = 512 * 1024 * 1024
    resp = _post_file(client, extra={"conversation_id": "7"})
    assert resp.status_code == 413
    assert "storage limit" in resp.get_json()["error"]
    service.create_attachment.assert_not_called()


def test_over_quota_creates_no_conversation(client, service):
    # On a brand-new chat the quota check must fire BEFORE the conversation is
    # created, or an over-quota attach leaves an empty conversation behind.
    service.bytes_for_owner.return_value = 512 * 1024 * 1024
    resp = _post_file(client)          # no conversation_id
    assert resp.status_code == 413
    client._create_conv.assert_not_called()
    service.create_attachment.assert_not_called()


def _make_client(cfg, service):
    app = Flask(__name__)
    app.secret_key = "test"
    create_conv = MagicMock(return_value=1)
    register_attachments(
        app, service=service, perm_decorator=lambda f: f,
        create_conversation_fn=create_conv, attachments_config=cfg,
    )
    app.config["TESTING"] = True
    c = app.test_client()
    c._create_conv = create_conv
    return c


def test_quota_disabled_allows_usage_over_positive_cap():
    svc = MagicMock()
    svc.verify_conversation_access.return_value = True
    svc.count_for_conversation.return_value = 0
    svc.bytes_for_owner.return_value = 10 ** 12          # would blow any positive cap
    svc.create_attachment.return_value = {"attachment_id": "u", "created_at": None}
    cfg = dict(CFG, max_total_mb_per_user=0)             # 0 disables the quota
    c = _make_client(cfg, svc)
    resp = c.post(
        "/api/chat/attachments",
        data={"client_id": "x", "conversation_id": "7",
              "file": (io.BytesIO(b"hello"), "n.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    svc.bytes_for_owner.assert_not_called()             # short-circuited when disabled


# --- Bounded read: oversize rejected before extract runs (A1) ---------------

def test_oversize_rejected_before_extract(monkeypatch):
    import src.interfaces.chat_app.attachment_routes as routes
    called = {"extract": 0}

    def fake_extract(filename, data, cfg):
        called["extract"] += 1
        return SimpleNamespace(kind="document", text="x",
                               meta={"extension": ".txt"}, warnings=[])

    monkeypatch.setattr(routes, "extract_attachment", fake_extract)
    svc = MagicMock()
    svc.verify_conversation_access.return_value = True
    svc.count_for_conversation.return_value = 0
    svc.bytes_for_owner.return_value = 0
    cfg = dict(CFG, max_file_mb=0)                       # cap = 0 bytes; any content overflows
    c = _make_client(cfg, svc)
    resp = c.post(
        "/api/chat/attachments",
        data={"client_id": "x", "conversation_id": "7",
              "file": (io.BytesIO(b"hello"), "n.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 413
    assert "0 MB limit" in resp.get_json()["error"]
    assert called["extract"] == 0                        # never buffered-then-decoded
    svc.create_attachment.assert_not_called()
