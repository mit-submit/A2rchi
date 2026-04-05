import sys
from unittest.mock import MagicMock, patch

from flask import Flask


def _secret_reader(values):
    def _read_secret(name, default=""):
        return values.get(name, default)

    return _read_secret


def test_resolve_data_manager_service_token_prefers_explicit_token():
    from src.utils.internal_auth import resolve_data_manager_service_token

    with patch("src.utils.internal_auth.read_secret", side_effect=_secret_reader({
        "DM_API_TOKEN": "dm-explicit-token",
        "PG_PASSWORD": "postgres-password",
    })):
        token, source = resolve_data_manager_service_token()

    assert token == "dm-explicit-token"
    assert source == "DM_API_TOKEN"


def test_resolve_data_manager_service_token_falls_back_to_pg_password():
    from src.utils.internal_auth import resolve_data_manager_service_token

    with patch("src.utils.internal_auth.read_secret", side_effect=_secret_reader({
        "PG_PASSWORD": "postgres-password",
    })):
        token, source = resolve_data_manager_service_token()

    assert token == "33d35de0f59af35976eb8d71af05e4da047068346ee196f078f0e7113ecd2328"
    assert source == "derived-from-PG_PASSWORD"


def test_require_admin_accepts_internal_bearer_token():
    from src.utils.internal_auth import resolve_data_manager_service_token

    app = Flask(__name__)
    app.secret_key = "test-secret"

    with patch.dict(sys.modules, {"spacy": MagicMock()}):
        from src.interfaces.uploader_app.app import FlaskAppWrapper

    dummy_wrapper = type("DummyWrapper", (), {})()
    dummy_wrapper.auth_enabled = True
    with patch("src.utils.internal_auth.read_secret", side_effect=_secret_reader({
        "PG_PASSWORD": "postgres-password",
    })):
        dummy_wrapper.api_token, dummy_wrapper.api_token_source = resolve_data_manager_service_token()

    def handler():
        return "ok"

    protected = FlaskAppWrapper.require_admin(dummy_wrapper, handler)

    with app.test_request_context("/", headers={"Authorization": f"Bearer {dummy_wrapper.api_token}"}):
        assert protected() == "ok"
