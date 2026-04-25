"""
Flask test client smoke tests for the /v1 OpenAI-compatible API.

Tests the HTTP layer: routing, request validation, auth middleware,
streaming SSE format, non-streaming JSON format, and error responses.
Uses Flask's built-in test client with mocked ChatWrapper and UserService.
"""

import json
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

flask = pytest.importorskip("flask", reason="Flask not installed")
Flask = flask.Flask

import src.interfaces.chat_app.openai_compat as compat
from src.interfaces.chat_app.openai_compat import register_openai_compat


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@dataclass
class FakeUser:
    id: str = "test-user"
    display_name: Optional[str] = "Test"
    email: Optional[str] = "test@example.com"
    auth_provider: str = "basic"
    is_admin: bool = False
    theme: str = "system"
    preferred_model: Optional[str] = None
    preferred_temperature: Optional[float] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _make_mock_chat_wrapper(events=None, raises=None):
    """Create a mock ChatWrapper that yields controlled events."""
    mock = MagicMock()
    mock.pg_config = {"host": "localhost", "dbname": "test"}

    if raises:
        mock.stream.side_effect = raises
    elif events is not None:
        mock.stream.return_value = iter(events)
    else:
        # Default: yield a chunk then a final
        mock.stream.return_value = iter([
            {"type": "chunk", "content": "Hello world"},
            {
                "type": "final",
                "response": "Hello world",
                "source_documents": [],
                "retriever_scores": [],
                "conversation_id": 1,
            },
        ])

    return mock


def _make_mock_user_service(valid_token="archi_test123", user=None):
    """Create a mock UserService with configurable token lookup."""
    mock = MagicMock()
    fake_user = user or FakeUser()

    def lookup(token, **kwargs):
        if token == valid_token:
            return fake_user
        return None

    mock.get_user_by_api_token.side_effect = lookup
    return mock


@pytest.fixture
def app_no_auth():
    """Flask test app with auth disabled."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    chat_wrapper = _make_mock_chat_wrapper()

    register_openai_compat(app, chat_wrapper, auth_enabled=False)
    yield app, chat_wrapper

    # Reset module globals
    compat._chat_wrapper = None
    compat._user_service = None
    compat._auth_enabled = False
    compat._token_ttl_days = 90


@pytest.fixture
def app_with_auth():
    """Flask test app with auth enabled."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    chat_wrapper = _make_mock_chat_wrapper()
    user_service = _make_mock_user_service()

    register_openai_compat(
        app, chat_wrapper,
        user_service=user_service,
        auth_enabled=True,
    )
    yield app, chat_wrapper, user_service

    compat._chat_wrapper = None
    compat._user_service = None
    compat._auth_enabled = False
    compat._token_ttl_days = 90


# We need to patch get_full_config at the point of use (in the route handlers)
# since it's imported inline. Use autouse for the no-auth tests.

@pytest.fixture(autouse=True)
def _patch_config():
    """Patch get_full_config for all tests so route handlers can resolve models."""
    with patch("src.utils.config_access.get_full_config",
               return_value={"name": "test-model"}):
        yield


@pytest.fixture(autouse=True)
def _patch_rbac():
    """Patch RBAC functions so auth checks work without a real registry."""
    mock_registry = MagicMock()
    mock_registry.default_role = "base-user"
    mock_registry.allow_anonymous = False

    with patch("src.interfaces.chat_app.openai_compat.get_registry",
               return_value=mock_registry), \
         patch("src.interfaces.chat_app.openai_compat.has_permission",
               return_value=True):
        yield


# ---------------------------------------------------------------------------
# GET /v1/models
# ---------------------------------------------------------------------------

class TestListModels:

    def test_returns_model_list(self, app_no_auth):
        app, _ = app_no_auth
        client = app.test_client()
        resp = client.get("/v1/models")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)
        assert len(data["data"]) >= 1

        model = data["data"][0]
        assert "id" in model
        assert model["object"] == "model"
        assert "created" in model
        assert model["owned_by"] == "archi"

    def test_returns_401_when_auth_enabled_no_token(self, app_with_auth):
        app, _, _ = app_with_auth
        client = app.test_client()
        resp = client.get("/v1/models")

        assert resp.status_code == 401
        data = resp.get_json()
        assert "error" in data
        assert data["error"]["type"] == "invalid_request_error"


# ---------------------------------------------------------------------------
# POST /v1/chat/completions — validation
# ---------------------------------------------------------------------------

class TestChatCompletionsValidation:

    def test_missing_model_returns_400(self, app_no_auth):
        app, _ = app_no_auth
        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"messages": [{"role": "user", "content": "hi"}]})

        assert resp.status_code == 400
        data = resp.get_json()
        assert "error" in data
        assert "'model'" in data["error"]["message"]

    def test_unknown_model_returns_404(self, app_no_auth):
        app, _ = app_no_auth
        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "nonexistent",
                                 "messages": [{"role": "user", "content": "hi"}]})

        assert resp.status_code == 404
        data = resp.get_json()
        assert "error" in data

    def test_missing_messages_returns_400(self, app_no_auth):
        app, _ = app_no_auth
        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "test-model"})

        assert resp.status_code == 400
        data = resp.get_json()
        assert "'messages'" in data["error"]["message"]


# ---------------------------------------------------------------------------
# Non-streaming response
# ---------------------------------------------------------------------------

class TestNonStreamingResponse:

    def test_valid_request_returns_completion(self, app_no_auth):
        app, chat_wrapper = app_no_auth
        # Reset the mock to return fresh events
        chat_wrapper.stream.return_value = iter([
            {"type": "chunk", "content": "Test answer"},
            {"type": "final", "response": "Test answer",
             "source_documents": [], "retriever_scores": []},
        ])

        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "test-model",
                                 "messages": [{"role": "user", "content": "hi"}],
                                 "stream": False})

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["object"] == "chat.completion"
        assert len(data["choices"]) == 1
        assert data["choices"][0]["message"]["content"] == "Test answer"
        assert data["choices"][0]["finish_reason"] == "stop"
        assert data["choices"][0]["message"]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Streaming response
# ---------------------------------------------------------------------------

class TestStreamingResponse:

    def test_valid_streaming_request(self, app_no_auth):
        app, chat_wrapper = app_no_auth
        chat_wrapper.stream.return_value = iter([
            {"type": "chunk", "content": "Hello"},
            {"type": "chunk", "content": " world"},
            {"type": "final", "response": "Hello world",
             "source_documents": [], "retriever_scores": []},
        ])

        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "test-model",
                                 "messages": [{"role": "user", "content": "hi"}],
                                 "stream": True})

        assert resp.status_code == 200
        assert "text/event-stream" in resp.content_type

        # Parse SSE lines
        raw = resp.get_data(as_text=True)
        lines = [l for l in raw.strip().split("\n") if l.startswith("data: ")]

        # Should have: 2 content chunks + 1 finish + [DONE]
        assert len(lines) >= 3
        assert lines[-1] == "data: [DONE]"

        # Verify content chunks are valid JSON
        for line in lines[:-1]:  # Skip [DONE]
            payload = json.loads(line[len("data: "):])
            assert payload["object"] == "chat.completion.chunk"
            assert "choices" in payload
            assert len(payload["choices"]) == 1


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

class TestAuthMiddleware:

    def test_no_token_when_auth_enabled_returns_401(self, app_with_auth):
        app, _, _ = app_with_auth
        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "test-model",
                                 "messages": [{"role": "user", "content": "hi"}]})

        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, app_with_auth):
        app, _, _ = app_with_auth
        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "test-model",
                                 "messages": [{"role": "user", "content": "hi"}]},
                           headers={"Authorization": "Bearer archi_invalid"})

        assert resp.status_code == 401

    def test_valid_token_allows_access(self, app_with_auth):
        """A valid bearer token is sufficient to access /v1."""
        app, chat_wrapper, user_service = app_with_auth
        user_service.get_user_by_api_token.side_effect = lambda t, **kw: FakeUser(id="token-user") if t == "archi_test123" else None
        chat_wrapper.stream.return_value = iter([
            {"type": "chunk", "content": "ok"},
            {"type": "final", "response": "ok",
             "source_documents": [], "retriever_scores": []},
        ])

        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "test-model",
                                 "messages": [{"role": "user", "content": "hi"}],
                                 "stream": False},
                           headers={"Authorization": "Bearer archi_test123"})

        assert resp.status_code == 200

    def test_non_admin_user_token_is_allowed(self, app_with_auth):
        """Non-admin users can use /v1 when they present a valid token."""
        app, _, user_service = app_with_auth
        regular_user = FakeUser(id="regular-user", is_admin=False)
        user_service.get_user_by_api_token.side_effect = lambda t, **kw: regular_user if t == "archi_test123" else None

        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "test-model",
                                 "messages": [{"role": "user", "content": "hi"}],
                                 "stream": False},
                           headers={"Authorization": "Bearer archi_test123"})

        assert resp.status_code == 200

    @patch("src.interfaces.chat_app.openai_compat.log_authentication_event")
    def test_successful_auth_logs_audit_event(self, mock_log, app_with_auth):
        """Successful auth produces an audit log entry."""
        app, chat_wrapper, _ = app_with_auth
        chat_wrapper.stream.return_value = iter([
            {"type": "chunk", "content": "ok"},
            {"type": "final", "response": "ok",
             "source_documents": [], "retriever_scores": []},
        ])

        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "test-model",
                                 "messages": [{"role": "user", "content": "hi"}],
                                 "stream": False},
                           headers={"Authorization": "Bearer archi_test123"})

        assert resp.status_code == 200
        # Find the success call among all calls
        success_calls = [c for c in mock_log.call_args_list if c.kwargs.get("success") is True]
        assert len(success_calls) >= 1

    @patch("src.interfaces.chat_app.openai_compat.log_authentication_event")
    def test_failed_auth_logs_audit_event(self, mock_log, app_with_auth):
        """Failed auth produces an audit log entry."""
        app, _, _ = app_with_auth
        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "test-model",
                                 "messages": [{"role": "user", "content": "hi"}]},
                           headers={"Authorization": "Bearer archi_invalid"})

        assert resp.status_code == 401
        failure_calls = [c for c in mock_log.call_args_list if c.kwargs.get("success") is False]
        assert len(failure_calls) >= 1

    def test_auth_disabled_allows_no_token(self, app_no_auth):
        app, chat_wrapper = app_no_auth
        chat_wrapper.stream.return_value = iter([
            {"type": "chunk", "content": "ok"},
            {"type": "final", "response": "ok",
             "source_documents": [], "retriever_scores": []},
        ])

        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "test-model",
                                 "messages": [{"role": "user", "content": "hi"}],
                                 "stream": False})

        assert resp.status_code == 200

    def test_anonymous_access_allowed_when_allow_anonymous_true(self, app_with_auth):
        """No token + allow_anonymous=True on registry -> 200 (anonymous access)."""
        app, chat_wrapper, _ = app_with_auth
        chat_wrapper.stream.return_value = iter([
            {"type": "chunk", "content": "ok"},
            {"type": "final", "response": "ok",
             "source_documents": [], "retriever_scores": []},
        ])

        mock_registry = MagicMock()
        mock_registry.allow_anonymous = True
        mock_registry.default_role = "base-user"

        with patch("src.interfaces.chat_app.openai_compat.get_registry",
                   return_value=mock_registry), \
             patch("src.interfaces.chat_app.openai_compat.has_permission",
                   return_value=True):
            client = app.test_client()
            resp = client.post("/v1/chat/completions",
                               json={"model": "test-model",
                                     "messages": [{"role": "user", "content": "hi"}],
                                     "stream": False})

        assert resp.status_code == 200

    def test_anonymous_access_rejected_when_allow_anonymous_false(self, app_with_auth):
        """No token + allow_anonymous=False on registry -> 401."""
        app, _, _ = app_with_auth

        mock_registry = MagicMock()
        mock_registry.allow_anonymous = False
        mock_registry.default_role = "base-user"

        with patch("src.interfaces.chat_app.openai_compat.get_registry",
                   return_value=mock_registry):
            client = app.test_client()
            resp = client.post("/v1/chat/completions",
                               json={"model": "test-model",
                                     "messages": [{"role": "user", "content": "hi"}]})

        assert resp.status_code == 401

    def test_authenticated_user_preferred_over_anonymous(self, app_with_auth):
        """Valid token + allow_anonymous=True -> 200 as authenticated user, not anonymous."""
        app, chat_wrapper, _ = app_with_auth
        chat_wrapper.stream.return_value = iter([
            {"type": "chunk", "content": "ok"},
            {"type": "final", "response": "ok",
             "source_documents": [], "retriever_scores": []},
        ])

        mock_registry = MagicMock()
        mock_registry.allow_anonymous = True
        mock_registry.default_role = "base-user"

        with patch("src.interfaces.chat_app.openai_compat.get_registry",
                   return_value=mock_registry), \
             patch("src.interfaces.chat_app.openai_compat.has_permission",
                   return_value=True):
            client = app.test_client()
            resp = client.post("/v1/chat/completions",
                               json={"model": "test-model",
                                     "messages": [{"role": "user", "content": "hi"}],
                                     "stream": False},
                               headers={"Authorization": "Bearer archi_test123"})

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def test_pipeline_exception_returns_500(self, app_no_auth):
        app, chat_wrapper = app_no_auth
        chat_wrapper.stream.side_effect = RuntimeError("Pipeline exploded")

        client = app.test_client()
        resp = client.post("/v1/chat/completions",
                           json={"model": "test-model",
                                 "messages": [{"role": "user", "content": "hi"}],
                                 "stream": False})

        assert resp.status_code == 500
        data = resp.get_json()
        assert "error" in data
        assert data["error"]["type"] == "server_error"
        assert "server error; see chat logs for message" in data["error"]["message"]
