"""Unit tests for CopilotAgentPipeline helper functions.

Tests the provider mapping, history formatter, MCP config passthrough,
and tool registry without requiring the Copilot SDK to be installed.
"""

import pytest

from src.archi.pipelines.copilot_agent import (
    _build_mcp_servers,
    _build_sdk_provider,
    _format_history_as_preamble,
)


class TestProviderMapping:
    """Decision 4: BYOK provider mapping."""

    def test_openai_provider(self):
        result = _build_sdk_provider("openai", "gpt-4o", {}, api_key="sk-test")
        assert result["type"] == "openai"
        assert result["model"] == "gpt-4o"
        assert result["api_key"] == "sk-test"

    def test_anthropic_provider(self):
        result = _build_sdk_provider("anthropic", "claude-sonnet-4-20250514", {}, api_key="key")
        assert result["type"] == "anthropic"
        assert result["model"] == "claude-sonnet-4-20250514"

    def test_openrouter_maps_to_openai(self):
        cfg = {"openrouter": {"base_url": "https://openrouter.ai/api/v1"}}
        result = _build_sdk_provider("openrouter", "google/gemini-2.0-flash", cfg, api_key="or-key")
        assert result["type"] == "openai"
        assert result["base_url"] == "https://openrouter.ai/api/v1"
        assert result["api_key"] == "or-key"

    def test_local_ollama_maps_to_openai(self):
        cfg = {"local": {"base_url": "http://localhost:11434/v1"}}
        result = _build_sdk_provider("local", "llama3", cfg)
        assert result["type"] == "openai"
        assert result["base_url"] == "http://localhost:11434/v1"
        assert "api_key" not in result  # Ollama doesn't need one

    def test_unsupported_provider_raises(self):
        with pytest.raises(ValueError, match="cannot be mapped"):
            _build_sdk_provider("gemini", "gemini-pro", {})

    def test_base_url_from_config(self):
        cfg = {"openai": {"base_url": "https://custom.endpoint/v1"}}
        result = _build_sdk_provider("openai", "gpt-4o", cfg, api_key="k")
        assert result["base_url"] == "https://custom.endpoint/v1"


class TestHistoryFormatter:
    """Task 3.4: conversation history → system message preamble."""

    def test_empty_history(self):
        assert _format_history_as_preamble([]) == ""
        assert _format_history_as_preamble(None) == ""

    def test_single_user_message(self):
        result = _format_history_as_preamble([("user", "Hello")])
        assert "<conversation_history>" in result
        assert "[user]: Hello" in result
        assert "</conversation_history>" in result

    def test_multi_turn(self):
        history = [
            ("user", "Hi"),
            ("assistant", "Hello!"),
            ("user", "How are you?"),
        ]
        result = _format_history_as_preamble(history)
        assert "[user]: Hi" in result
        assert "[assistant]: Hello!" in result
        assert "[user]: How are you?" in result

    def test_speaker_normalization(self):
        history = [("Human", "test"), ("AI", "response")]
        result = _format_history_as_preamble(history)
        assert "[user]: test" in result
        assert "[assistant]: response" in result


class TestMCPPassthrough:
    """Decision 8: MCP config mapping."""

    def test_no_mcp_config(self):
        assert _build_mcp_servers({}) is None
        assert _build_mcp_servers({"other": "stuff"}) is None

    def test_stdio_server(self):
        config = {
            "mcp_servers": {
                "my_server": {
                    "transport": "stdio",
                    "command": "uvx",
                    "args": ["mcp-server-example"],
                }
            }
        }
        result = _build_mcp_servers(config)
        assert result is not None
        assert result["my_server"]["type"] == "stdio"
        assert result["my_server"]["command"] == "uvx"
        assert "transport" not in result["my_server"]

    def test_sse_server(self):
        config = {
            "mcp_servers": {
                "web_search": {
                    "transport": "sse",
                    "url": "http://localhost:8080/sse",
                }
            }
        }
        result = _build_mcp_servers(config)
        assert result["web_search"]["type"] == "sse"
        assert result["web_search"]["url"] == "http://localhost:8080/sse"

    def test_multiple_servers(self):
        config = {
            "mcp_servers": {
                "a": {"transport": "stdio", "command": "cmd_a"},
                "b": {"transport": "sse", "url": "http://b:8080"},
            }
        }
        result = _build_mcp_servers(config)
        assert len(result) == 2
        assert "a" in result
        assert "b" in result


class TestToolRegistry:
    """Decision 17: TOOL_REGISTRY from tools module."""

    def test_registry_has_expected_tools(self):
        from src.archi.tools import TOOL_REGISTRY

        expected = {
            "search_knowledge_base",
            "search_local_files",
            "search_metadata_index",
            "list_metadata_schema",
            "fetch_catalog_document",
            "monit_opensearch_search",
            "monit_opensearch_aggregation",
        }
        assert expected == set(TOOL_REGISTRY.keys())

    def test_registry_entries_have_factory_and_description(self):
        from src.archi.tools import TOOL_REGISTRY

        for name, entry in TOOL_REGISTRY.items():
            assert "factory" in entry, f"{name} missing factory"
            assert "description" in entry, f"{name} missing description"
            assert callable(entry["factory"]), f"{name} factory not callable"
            assert isinstance(entry["description"], str), f"{name} description not str"
