from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask

from src.interfaces.chat_app.app import ConversationAccessError, FlaskAppWrapper


def _wrapper(trace, source_path=None, enabled=True):
    wrapper = object.__new__(FlaskAppWrapper)
    wrapper.app = Flask(__name__)
    wrapper.app.secret_key = "test-secret"
    wrapper.data_path = "/tmp"
    wrapper.pg_config = {}
    wrapper.chat = SimpleNamespace(
        get_agent_trace=Mock(return_value=trace),
        get_trace_by_message=Mock(return_value=trace),
        query_conversation_history=Mock(return_value=[]),
    )
    catalog = SimpleNamespace(
        get_filepath_for_hash=Mock(return_value=source_path),
        is_document_enabled=Mock(return_value=enabled),
    )
    wrapper._catalog_for_evidence = Mock(return_value=catalog)
    return wrapper


def _trace(item):
    return {
        "trace_id": "trace-1",
        "conversation_id": 123,
        "events": [
            {
                "type": "retrieved_evidence",
                "evidence": {
                    "version": 1,
                    "items": [item],
                    "groups": [
                        {
                            "resource_hash": item["source"]["resource_hash"],
                            "display_name": item["source"]["display_name"],
                            "items": [item],
                        }
                    ],
                },
            }
        ],
    }


def _item(item_id="item-1", kind="text", preview_type="text", resource_hash="hash-1"):
    return {
        "id": item_id,
        "kind": kind,
        "excerpt": "Retrieved excerpt",
        "page": {"page_number": 1, "page_index": 0} if preview_type == "pdf_page" else None,
        "source": {
            "resource_hash": resource_hash,
            "display_name": "source.txt",
            "source_url": "https://example.test/source",
        },
        "preview": {
            "type": preview_type,
            "available": preview_type != "unsupported",
            "preview_unavailable": preview_type == "unsupported",
        },
        "actions": {
            "source_url": "https://example.test/source",
            "download_url": f"/api/evidence/{item_id}/download",
        },
    }


def test_get_evidence_by_trace_returns_payload():
    item = _item()
    wrapper = _wrapper(_trace(item))

    with wrapper.app.test_request_context("/api/evidence/trace/trace-1?client_id=client-1"):
        response, status = FlaskAppWrapper.get_evidence_by_trace(wrapper, "trace-1")

    assert status == 200
    assert response.get_json()["evidence"]["items"][0]["id"] == "item-1"


def test_text_preview_returns_escaped_json_payload():
    item = _item(preview_type="text")
    wrapper = _wrapper(_trace(item))

    with wrapper.app.test_request_context("/api/evidence/item-1/preview?trace_id=trace-1&client_id=client-1"):
        response, status = FlaskAppWrapper.preview_evidence_item(wrapper, "item-1")

    assert status == 200
    assert response.get_json()["type"] == "text"
    assert response.get_json()["excerpt"] == "Retrieved excerpt"


def test_unsupported_preview_returns_unavailable_metadata():
    item = _item(kind="unsupported", preview_type="unsupported")
    wrapper = _wrapper(_trace(item))

    with wrapper.app.test_request_context("/api/evidence/item-1/preview?trace_id=trace-1&client_id=client-1"):
        response, status = FlaskAppWrapper.preview_evidence_item(wrapper, "item-1")

    assert status == 200
    assert response.get_json()["type"] == "preview_unavailable"
    assert response.get_json()["metadata"]["id"] == "item-1"


def test_missing_visual_source_returns_clear_unavailable_response():
    item = _item(kind="image", preview_type="image")
    wrapper = _wrapper(_trace(item), source_path=None)

    with wrapper.app.test_request_context("/api/evidence/item-1/preview?trace_id=trace-1&client_id=client-1"):
        response, status = FlaskAppWrapper.preview_evidence_item(wrapper, "item-1")

    assert status == 404
    payload = response.get_json()
    assert payload["type"] == "preview_unavailable"
    assert "source file is unavailable" in payload["reason"]


def test_image_preview_streams_original_file(tmp_path):
    image_path = tmp_path / "source.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    item = _item(kind="image", preview_type="image")
    wrapper = _wrapper(_trace(item), source_path=image_path)

    with wrapper.app.test_request_context("/api/evidence/item-1/preview?trace_id=trace-1&client_id=client-1"):
        response = FlaskAppWrapper.preview_evidence_item(wrapper, "item-1")

    assert response.status_code == 200
    assert response.mimetype == "image/png"


def test_pdf_page_preview_renders_png(tmp_path):
    import fitz

    pdf_path = tmp_path / "source.pdf"
    doc = fitz.open()
    doc.new_page(width=72, height=72)
    doc.save(str(pdf_path))
    doc.close()
    item = _item(kind="pdf_page_text", preview_type="pdf_page")
    wrapper = _wrapper(_trace(item), source_path=pdf_path)

    with wrapper.app.test_request_context("/api/evidence/item-1/preview?trace_id=trace-1&client_id=client-1"):
        response = FlaskAppWrapper.preview_evidence_item(wrapper, "item-1")

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.get_data().startswith(b"\x89PNG")


def test_download_endpoint_streams_original_file(tmp_path):
    source_path = tmp_path / "source.txt"
    source_path.write_text("download me")
    item = _item()
    wrapper = _wrapper(_trace(item), source_path=source_path)

    with wrapper.app.test_request_context("/api/evidence/item-1/download?trace_id=trace-1&client_id=client-1"):
        response = FlaskAppWrapper.download_evidence_source(wrapper, "item-1")

    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]


def test_unauthorized_trace_request_is_rejected():
    item = _item()
    wrapper = _wrapper(_trace(item))
    wrapper.chat.query_conversation_history.side_effect = ConversationAccessError("nope")

    with wrapper.app.test_request_context("/api/evidence/trace/trace-1?client_id=client-1"):
        response, status = FlaskAppWrapper.get_evidence_by_trace(wrapper, "trace-1")

    assert status == 403
    assert response.get_json()["error"] == "Forbidden"


def test_disabled_document_preview_is_rejected(tmp_path):
    source_path = tmp_path / "source.txt"
    source_path.write_text("secret")
    item = _item()
    wrapper = _wrapper(_trace(item), source_path=source_path, enabled=False)

    with wrapper.app.test_request_context("/api/evidence/item-1/preview?trace_id=trace-1&client_id=client-1"):
        response, status = FlaskAppWrapper.preview_evidence_item(wrapper, "item-1")

    assert status == 403
    assert response.get_json()["error"] == "Forbidden"
