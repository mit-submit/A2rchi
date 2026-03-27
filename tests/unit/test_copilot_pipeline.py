"""Unit tests for CopilotAgentPipeline helper functions.

Tests the provider mapping, history formatter, MCP config passthrough,
and tool registry without requiring the Copilot SDK to be installed.
"""

import pytest

from src.archi.pipelines.copilot_agent import (
    _build_tool_restriction_kwargs,
    _build_mcp_servers,
    _build_sdk_provider,
)


class TestProviderMapping:
    """Decision 4: BYOK provider mapping."""

    def test_openai_provider(self):
        result = _build_sdk_provider("openai", "gpt-4o", {}, api_key="sk-test")
        assert result["type"] == "openai"
        assert result["api_key"] == "sk-test"
        # model is passed separately to create_session, not in provider dict
        assert "model" not in result

    def test_anthropic_provider(self):
        result = _build_sdk_provider("anthropic", "claude-sonnet-4-20250514", {}, api_key="key")
        assert result["type"] == "anthropic"
        assert result["api_key"] == "key"
        assert "model" not in result

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


class TestToolNameAliases:
    """Tool-name alias normalization in _build_tools."""

    def test_search_vectorstore_hybrid_normalizes(self):
        """Agent specs with 'search_vectorstore_hybrid' should match
        the canonical 'search_knowledge_base' tool."""
        from src.archi.pipelines.copilot_agent import CopilotAgentPipeline

        # Build a bare pipeline with the old tool name
        pipeline = CopilotAgentPipeline.__new__(CopilotAgentPipeline)
        pipeline.selected_tool_names = ["search_vectorstore_hybrid", "search_local_files"]
        pipeline.dm_config = {}
        pipeline._catalog_client = None
        pipeline._monit_client = None

        # _build_tools needs a collector; tools will be empty because there's
        # no vectorstore or catalog client, but the alias resolution itself
        # should not raise.
        from src.archi.tools import DocumentCollector
        tools = pipeline._build_tools(DocumentCollector())
        # No tools built (no vectorstore/catalog), but no crash either
        assert isinstance(tools, list)

    def test_canonical_name_still_works(self):
        """The canonical 'search_knowledge_base' should still work."""
        from src.archi.pipelines.copilot_agent import CopilotAgentPipeline

        pipeline = CopilotAgentPipeline.__new__(CopilotAgentPipeline)
        pipeline.selected_tool_names = ["search_knowledge_base"]
        pipeline.dm_config = {}
        pipeline._catalog_client = None
        pipeline._monit_client = None

        from src.archi.tools import DocumentCollector
        tools = pipeline._build_tools(DocumentCollector())
        assert isinstance(tools, list)


class TestToolRestrictions:
    """SDK built-in tools must be hard-blocked for Archi sessions."""

    def test_allowlist_contains_only_custom_tool_names(self):
        """available_tools should list exactly the custom tools passed in."""
        from unittest.mock import MagicMock

        tool_a = MagicMock()
        tool_a.name = "search_knowledge_base"
        tool_b = MagicMock()
        tool_b.name = "rucio_events_search"

        kwargs = _build_tool_restriction_kwargs([tool_a, tool_b])
        assert sorted(kwargs["available_tools"]) == [
            "rucio_events_search",
            "search_knowledge_base",
        ]
        assert "excluded_tools" not in kwargs

    def test_empty_tools_yields_empty_allowlist(self):
        """When no custom tools exist, available_tools is empty — blocking everything."""
        kwargs = _build_tool_restriction_kwargs([])
        assert kwargs["available_tools"] == []
        assert "excluded_tools" not in kwargs


class TestPermissionRequests:
    """Only declared Archi custom tools should be approved."""

    def _make_pipeline(self, selected_tool_names=None):
        from src.archi.pipelines.copilot_agent import CopilotAgentPipeline

        pipeline = CopilotAgentPipeline.__new__(CopilotAgentPipeline)
        pipeline.selected_tool_names = list(selected_tool_names or [])
        return pipeline

    def test_approves_allowed_custom_tool(self):
        from copilot.generated.session_events import PermissionRequest, PermissionRequestKind

        pipeline = self._make_pipeline(["search_local_files"])
        request = PermissionRequest(kind=PermissionRequestKind.CUSTOM_TOOL, tool_name="search_local_files")

        result = pipeline._on_permission_request(request, {"toolCallId": "1"})

        assert result.kind == "approved"

    def test_denies_builtin_shell_request(self):
        from copilot.generated.session_events import PermissionRequest, PermissionRequestKind

        pipeline = self._make_pipeline(["search_local_files"])
        request = PermissionRequest(
            kind=PermissionRequestKind.SHELL,
            tool_name="bash",
            full_command_text="pwd",
        )

        result = pipeline._on_permission_request(request, {"toolCallId": "2"})

        assert result.kind == "denied"
        assert "Only Archi custom tools" in result.message

    def test_denies_custom_tool_not_in_agent_spec(self):
        from copilot.generated.session_events import PermissionRequest, PermissionRequestKind

        pipeline = self._make_pipeline(["search_local_files"])
        request = PermissionRequest(kind=PermissionRequestKind.CUSTOM_TOOL, tool_name="read_file")

        result = pipeline._on_permission_request(request, {"toolCallId": "3"})

        assert result.kind == "denied"
        assert "not allowed" in result.message


class TestGetToolRegistrySignature:
    """get_tool_registry and get_tool_descriptions must work when called
    via the same pattern as app.py: agent_cls.method(dummy_instance)."""

    def test_get_tool_registry_instance_call(self):
        from src.archi.pipelines.copilot_agent import CopilotAgentPipeline

        dummy = CopilotAgentPipeline.__new__(CopilotAgentPipeline)
        # This is how app.py calls it — must not raise
        registry = CopilotAgentPipeline.get_tool_registry(dummy)
        assert isinstance(registry, dict)
        assert "search_knowledge_base" in registry

    def test_get_tool_descriptions_instance_call(self):
        from src.archi.pipelines.copilot_agent import CopilotAgentPipeline

        dummy = CopilotAgentPipeline.__new__(CopilotAgentPipeline)
        descriptions = CopilotAgentPipeline.get_tool_descriptions(dummy)
        assert isinstance(descriptions, dict)
        assert "search_knowledge_base" in descriptions
        assert isinstance(descriptions["search_knowledge_base"], str)


class TestSessionConfigOverrides:
    """Bug #15/#16: per-request provider/model/api_key overrides."""

    def _make_pipeline(self):
        from src.archi.pipelines.copilot_agent import CopilotAgentPipeline
        p = CopilotAgentPipeline.__new__(CopilotAgentPipeline)
        p.default_provider = "openai"
        p.default_model = "gpt-4o"
        p._providers_config = {}
        p.agent_prompt = "You are a test bot"
        p.archi_config = {}
        return p

    def test_default_provider_used_when_no_override(self):
        p = self._make_pipeline()
        cfg = p._build_session_config(tools=[], api_key="sk-test")
        assert cfg["model"] == "gpt-4o"
        assert cfg["provider"]["type"] == "openai"
        assert cfg["provider"]["api_key"] == "sk-test"

    def test_provider_override(self):
        p = self._make_pipeline()
        cfg = p._build_session_config(
            tools=[],
            api_key="ant-key",
            provider_override="anthropic",
            model_override="claude-sonnet-4-20250514",
        )
        assert cfg["model"] == "claude-sonnet-4-20250514"
        assert cfg["provider"]["type"] == "anthropic"
        assert cfg["provider"]["api_key"] == "ant-key"

    def test_partial_override_only_model(self):
        """If only model is overridden, provider stays default."""
        p = self._make_pipeline()
        cfg = p._build_session_config(
            tools=[],
            api_key="k",
            model_override="gpt-4o-mini",
        )
        assert cfg["model"] == "gpt-4o-mini"
        assert cfg["provider"]["type"] == "openai"

    def test_partial_override_only_provider(self):
        """If only provider is overridden, model stays default."""
        p = self._make_pipeline()
        cfg = p._build_session_config(
            tools=[],
            api_key="k",
            provider_override="anthropic",
        )
        assert cfg["model"] == "gpt-4o"
        assert cfg["provider"]["type"] == "anthropic"

    def test_api_key_forwarded(self):
        """API key is forwarded to provider dict."""
        p = self._make_pipeline()
        cfg = p._build_session_config(tools=[], api_key="session-key-123")
        assert cfg["provider"]["api_key"] == "session-key-123"


class TestSessionResume:
    """Session resume failure should not reuse a bad session_id."""

    def test_session_id_cleared_on_resume_failure(self):
        """When resume_session() fails, the fallback create_session() should not
        reuse the old session_id that failed."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from src.archi.pipelines.copilot_agent import CopilotAgentPipeline

        p = CopilotAgentPipeline.__new__(CopilotAgentPipeline)
        p.default_provider = "openai"
        p.default_model = "gpt-4o"
        p._providers_config = {}
        p.agent_prompt = "test"
        p.archi_config = {}
        p.selected_tool_names = None

        mock_client = MagicMock()
        mock_client.resume_session = AsyncMock(side_effect=Exception("session not found"))
        mock_session = MagicMock()
        mock_client.create_session = AsyncMock(return_value=mock_session)
        p._client = mock_client

        adapter = MagicMock()

        loop = asyncio.new_event_loop()
        config = p._build_session_config(tools=[], api_key=None)
        session = loop.run_until_complete(
            p._create_session(adapter, config, session_id="bad-session-id")
        )
        loop.close()

        # create_session should NOT have session_id= in its kwargs
        call_kwargs = mock_client.create_session.call_args
        assert "session_id" not in call_kwargs.kwargs
        # But it should still have been called
        mock_client.create_session.assert_called_once()


class TestCustomizeMode:
    """System message uses customize mode with per-section overrides."""

    def _make_pipeline(self, prompt="You are a test bot"):
        from src.archi.pipelines.copilot_agent import CopilotAgentPipeline
        p = CopilotAgentPipeline.__new__(CopilotAgentPipeline)
        p.default_provider = "openai"
        p.default_model = "gpt-4o"
        p._providers_config = {}
        p.agent_prompt = prompt
        p.archi_config = {}
        return p

    def test_customize_mode_with_identity_section(self):
        p = self._make_pipeline("You are a CMS computing assistant")
        cfg = p._build_session_config(tools=[], api_key="k")

        sm = cfg["system_message"]
        assert sm["mode"] == "customize"
        assert "sections" in sm
        assert sm["sections"]["identity"]["action"] == "replace"
        assert sm["sections"]["identity"]["content"] == "You are a CMS computing assistant"

    def test_no_system_message_without_prompt(self):
        p = self._make_pipeline(prompt=None)
        cfg = p._build_session_config(tools=[], api_key="k")
        sm = cfg["system_message"]
        assert sm["mode"] == "customize"
        assert "identity" not in sm["sections"]
        assert sm["sections"]["tool_instructions"]["action"] == "append"

    def test_sdk_defaults_not_overridden(self):
        """safety, tool_efficiency, and code_change_rules should stay SDK-managed."""
        p = self._make_pipeline()
        cfg = p._build_session_config(tools=[], api_key="k")
        sections = cfg["system_message"]["sections"]
        for section in ("safety", "tool_efficiency", "code_change_rules"):
            assert section not in sections

    def test_tool_instructions_forbid_fake_shell_use(self):
        p = self._make_pipeline()
        cfg = p._build_session_config(tools=[], api_key="k")

        section = cfg["system_message"]["sections"]["tool_instructions"]
        assert section["action"] == "append"
        assert "Do not claim to have run bash" in section["content"]

    def test_no_history_in_system_message(self):
        """History is no longer injected — session persistence handles it."""
        p = self._make_pipeline()
        cfg = p._build_session_config(tools=[], api_key="k")
        sm = cfg["system_message"]
        # No content key at all, just sections
        assert "content" not in sm or "<conversation_history>" not in str(sm.get("content", ""))


class TestErrorHook:
    """onErrorOccurred hook: retry transient model errors."""

    def _make_pipeline(self):
        from src.archi.pipelines.copilot_agent import CopilotAgentPipeline
        p = CopilotAgentPipeline.__new__(CopilotAgentPipeline)
        return p

    def test_recoverable_model_error_returns_retry(self):
        p = self._make_pipeline()
        result = p._on_error_occurred({
            "error": "Rate limit exceeded",
            "errorContext": "model_call",
            "recoverable": True,
            "timestamp": 1,
            "cwd": "/",
        })
        assert result is not None
        assert result["errorHandling"] == "retry"
        assert result["retryCount"] == 2
        assert "retry" in result["userNotification"].lower()

    def test_non_recoverable_error_returns_none(self):
        p = self._make_pipeline()
        result = p._on_error_occurred({
            "error": "Invalid API key",
            "errorContext": "model_call",
            "recoverable": False,
            "timestamp": 1,
            "cwd": "/",
        })
        assert result is None

    def test_tool_execution_error_not_retried(self):
        p = self._make_pipeline()
        result = p._on_error_occurred({
            "error": "Tool crashed",
            "errorContext": "tool_execution",
            "recoverable": True,
            "timestamp": 1,
            "cwd": "/",
        })
        # Only model_call errors are retried
        assert result is None

    def test_system_error_not_retried(self):
        p = self._make_pipeline()
        result = p._on_error_occurred({
            "error": "System error",
            "errorContext": "system",
            "recoverable": True,
            "timestamp": 1,
            "cwd": "/",
        })
        assert result is None
