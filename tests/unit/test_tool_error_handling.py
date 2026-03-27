"""Unit tests for tool error handling.

Tests RemoteCatalogClient redirect detection (regression for bug #12),
HTTP error handling, and timeouts.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.archi.pipelines.agents.tools.local_files import RemoteCatalogClient


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_response(status_code, *, json_data=None, headers=None, is_redirect=False):
    """Create a mock requests.Response."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.is_redirect = is_redirect
    resp.headers = headers or {}
    if json_data is not None:
        resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            response=resp, request=MagicMock()
        )
    return resp


# ── Tests: Redirect detection ────────────────────────────────────────────

class TestRedirectDetection:
    """Regression for bug #12: catalog API returning 302 → login page
    was silently parsed as JSON, causing a confusing error."""

    def test_search_302_raises_runtime_error(self):
        """302 redirect on search must raise RuntimeError with clear message."""
        client = RemoteCatalogClient(base_url="http://test:7871")
        resp = _make_response(302, headers={"Location": "http://test:7871/login"}, is_redirect=True)

        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="redirected.*DM_API_TOKEN"):
                client.search("test query")

    def test_search_301_raises_runtime_error(self):
        client = RemoteCatalogClient(base_url="http://test:7871")
        resp = _make_response(301, headers={"Location": "/login"}, is_redirect=False)

        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="redirected"):
                client.search("test query")

    def test_search_307_raises_runtime_error(self):
        client = RemoteCatalogClient(base_url="http://test:7871")
        resp = _make_response(307, headers={"Location": "/auth"}, is_redirect=True)

        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="redirected"):
                client.search("test query")

    def test_get_document_302_raises(self):
        client = RemoteCatalogClient(base_url="http://test:7871")
        resp = _make_response(302, headers={"Location": "/login"}, is_redirect=True)

        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="redirected"):
                client.get_document("abc123")

    def test_schema_302_raises(self):
        client = RemoteCatalogClient(base_url="http://test:7871")
        resp = _make_response(302, headers={"Location": "/login"}, is_redirect=True)

        with patch("requests.get", return_value=resp):
            with pytest.raises(RuntimeError, match="redirected"):
                client.schema()


# ── Tests: Successful responses ──────────────────────────────────────────

class TestSuccessfulResponses:
    """Verify normal operations continue to work."""

    def test_search_200_returns_hits(self):
        client = RemoteCatalogClient(base_url="http://test:7871")
        resp = _make_response(200, json_data={"hits": [{"hash": "abc", "path": "/test.md"}]})

        with patch("requests.get", return_value=resp):
            results = client.search("test")

        assert len(results) == 1
        assert results[0]["hash"] == "abc"

    def test_search_200_empty_hits(self):
        client = RemoteCatalogClient(base_url="http://test:7871")
        resp = _make_response(200, json_data={"hits": []})

        with patch("requests.get", return_value=resp):
            results = client.search("nothing")

        assert results == []

    def test_get_document_200(self):
        client = RemoteCatalogClient(base_url="http://test:7871")
        resp = _make_response(200, json_data={"text": "hello", "metadata": {}})

        with patch("requests.get", return_value=resp):
            result = client.get_document("abc123")

        assert result["text"] == "hello"

    def test_get_document_404_returns_none(self):
        client = RemoteCatalogClient(base_url="http://test:7871")
        resp = _make_response(404)

        with patch("requests.get", return_value=resp):
            result = client.get_document("missing")

        assert result is None

    def test_schema_200(self):
        client = RemoteCatalogClient(base_url="http://test:7871")
        resp = _make_response(200, json_data={"keys": ["source_type"], "source_types": ["git"]})

        with patch("requests.get", return_value=resp):
            result = client.schema()

        assert "keys" in result


# ── Tests: HTTP errors ───────────────────────────────────────────────────

class TestHTTPErrors:
    """Verify proper error propagation for HTTP failures."""

    def test_search_500_raises(self):
        client = RemoteCatalogClient(base_url="http://test:7871")
        resp = _make_response(500)

        with patch("requests.get", return_value=resp):
            with pytest.raises(requests.HTTPError):
                client.search("test")

    def test_search_timeout_raises(self):
        client = RemoteCatalogClient(base_url="http://test:7871", timeout=0.1)

        with patch("requests.get", side_effect=requests.Timeout("timed out")):
            with pytest.raises(requests.Timeout):
                client.search("test")

    def test_connection_error_raises(self):
        client = RemoteCatalogClient(base_url="http://test:7871")

        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            with pytest.raises(requests.ConnectionError):
                client.search("test")


# ── Tests: Client construction ───────────────────────────────────────────

class TestClientConstruction:
    """Verify RemoteCatalogClient configuration."""

    def test_api_token_sets_auth_header(self):
        client = RemoteCatalogClient(base_url="http://test:7871", api_token="secret")
        assert client._headers["Authorization"] == "Bearer secret"

    def test_no_api_token_no_auth_header(self):
        client = RemoteCatalogClient(base_url="http://test:7871")
        assert "Authorization" not in client._headers

    def test_host_mode_uses_localhost(self):
        with patch.dict("os.environ", {"HOST_MODE": "true"}):
            client = RemoteCatalogClient(port=7871)
        assert "localhost" in client.base_url

    def test_non_host_mode_uses_data_manager(self):
        with patch.dict("os.environ", {}, clear=True):
            # Clear all HOST_MODE variants
            import os
            for key in ["HOST_MODE", "HOSTMODE", "ARCHI_HOST_MODE"]:
                os.environ.pop(key, None)
            client = RemoteCatalogClient(port=7871)
        assert "data-manager" in client.base_url

    @patch("src.archi.pipelines.agents.tools.local_files.read_secret", return_value="dm-token-123")
    def test_from_deployment_config(self, mock_read_secret):
        config = {
            "host_mode": True,
            "services": {
                "data_manager": {
                    "port": 7871,
                }
            }
        }
        client = RemoteCatalogClient.from_deployment_config(config)
        assert client._headers.get("Authorization") == "Bearer dm-token-123"


# ── Tests: Tool factory error handling ───────────────────────────────────

class TestToolFactoryErrorHandling:
    """Verify tool functions handle catalog errors gracefully."""

    def test_file_search_tool_catches_catalog_exception(self):
        """When catalog.search() raises, the tool should return an error
        message string, not propagate the exception."""
        client = MagicMock(spec=RemoteCatalogClient)
        client.search.side_effect = RuntimeError("redirected to /login")

        # Import the tool factory
        from src.archi.pipelines.agents.tools.local_files import create_file_search_tool
        tool = create_file_search_tool(client)

        # LangChain tool.invoke() should return error message
        result = tool.invoke({"query": "test"})
        assert "failed" in result.lower() or "error" in result.lower() or "unavailable" in result.lower()
