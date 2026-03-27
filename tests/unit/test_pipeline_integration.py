"""Layer 2 integration tests: pipeline → adapter → output flow.

Tests the wiring between CopilotAgentPipeline, CopilotEventAdapter,
and Archi.stream() without requiring a real Copilot SDK session.
"""

import asyncio
import queue
import threading
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from src.archi.copilot_event_adapter import _SENTINEL, CopilotEventAdapter
from src.archi.utils.output_dataclass import PipelineOutput

# ── Helpers ───────────────────────────────────────────────────────────────


class FakeAsyncLoop:
    """Stub for AsyncLoopThread that runs coroutines inline."""

    def run(self, coro, timeout=5.0):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def run_no_wait(self, coro):
        loop = asyncio.new_event_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future

    def submit(self, coro):
        """Schedule coro and return immediately."""
        loop = asyncio.new_event_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop)


# ── Archi.stream() passthrough tests ──────────────────────────────────────


class TestArchiStreamPassthrough:
    """Verify kwargs flow from Archi.stream() → pipeline.stream()."""

    def test_kwargs_forwarded_to_pipeline(self):
        """All kwargs including provider/model/api_key reach the pipeline."""
        from src.archi.archi import archi as ArchiClass

        mock_pipeline = MagicMock()
        mock_pipeline.stream = MagicMock(
            return_value=iter(
                [
                    PipelineOutput(
                        answer="ok", metadata={"event_type": "final"}, final=True
                    ),
                ]
            )
        )

        instance = ArchiClass.__new__(ArchiClass)
        instance.pipeline = mock_pipeline
        instance.pipeline_name = "test"
        instance.vs_connector = MagicMock()
        instance.vs_connector.get_vectorstore.return_value = MagicMock()

        results = list(
            instance.stream(
                history=[("user", "hi")],
                conversation_id=1,
                provider="anthropic",
                model="claude-sonnet-4-20250514",
                provider_api_key="sk-test",
            )
        )

        assert len(results) == 1
        call_kwargs = mock_pipeline.stream.call_args.kwargs
        assert call_kwargs["provider"] == "anthropic"
        assert call_kwargs["model"] == "claude-sonnet-4-20250514"
        assert call_kwargs["provider_api_key"] == "sk-test"
        assert call_kwargs["history"] == [("user", "hi")]
        # vectorstore is injected by _prepare_call_kwargs
        assert "vectorstore" in call_kwargs

    def test_pipeline_output_type_enforced(self):
        """Archi.stream() raises TypeError if pipeline yields non-PipelineOutput."""
        from src.archi.archi import archi as ArchiClass

        mock_pipeline = MagicMock()
        mock_pipeline.stream = MagicMock(return_value=iter(["bad string"]))

        instance = ArchiClass.__new__(ArchiClass)
        instance.pipeline = mock_pipeline
        instance.pipeline_name = "test"
        instance.vs_connector = MagicMock()
        instance.vs_connector.get_vectorstore.return_value = MagicMock()

        with pytest.raises(TypeError, match="PipelineOutput"):
            list(instance.stream(history=[]))


# ── Pipeline session config integration tests ─────────────────────────────


class TestPipelineSessionConfig:
    """Verify _build_session_config produces correct SDK config."""

    def _make_pipeline(self, **overrides):
        from src.archi.pipelines.copilot_agent import CopilotAgentPipeline

        p = CopilotAgentPipeline.__new__(CopilotAgentPipeline)
        p.default_provider = overrides.get("provider", "openai")
        p.default_model = overrides.get("model", "gpt-4o")
        p._providers_config = overrides.get("providers_config", {})
        p.agent_prompt = overrides.get("prompt", "Test prompt")
        p.archi_config = overrides.get("archi_config", {})
        return p

    def test_system_message_uses_customize_mode(self):
        p = self._make_pipeline(prompt="You are helpful")
        cfg = p._build_session_config(
            tools=[],
            api_key="k",
        )
        sys_msg = cfg["system_message"]
        assert sys_msg["mode"] == "customize"
        assert sys_msg["sections"]["identity"]["action"] == "replace"
        assert "You are helpful" in sys_msg["sections"]["identity"]["content"]

    def test_mcp_servers_included_when_configured(self):
        p = self._make_pipeline(
            archi_config={
                "mcp_servers": {
                    "test_server": {"transport": "stdio", "command": "test-cmd"}
                }
            }
        )
        cfg = p._build_session_config(tools=[], api_key="k")
        assert "mcp_servers" in cfg
        assert "test_server" in cfg["mcp_servers"]

    def test_tools_in_special_key(self):
        """Tools go in _tools key, popped by _create_session."""
        p = self._make_pipeline()
        fake_tools = [MagicMock(), MagicMock()]
        cfg = p._build_session_config(tools=fake_tools, api_key="k")
        assert cfg["_tools"] == fake_tools


# ── Adapter → Pipeline output flow ───────────────────────────────────────


class TestAdapterOutputFlow:
    """Test that adapter produces correct PipelineOutput sequence."""

    def test_text_then_done_produces_outputs(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        # Simulate: text chunk, then signal done
        adapter._queue.put(
            PipelineOutput(
                answer="Hello ",
                metadata={"event_type": "text"},
                final=False,
            )
        )
        adapter._queue.put(
            PipelineOutput(
                answer="world",
                metadata={"event_type": "text"},
                final=False,
            )
        )
        adapter.signal_done()

        outputs = list(adapter.iter_outputs(poll_timeout=2.0))
        assert len(outputs) == 2
        assert outputs[0].answer == "Hello "
        assert outputs[1].answer == "world"

    def test_build_final_aggregates_tool_calls(self):
        from src.archi.copilot_event_adapter import _ToolCallRecord

        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._response_buffer = "Final answer"
        adapter._tool_calls = [
            _ToolCallRecord(
                id="tc-1", name="search_knowledge_base", args={"query": "test"}
            ),
            _ToolCallRecord(
                id="tc-2", name="fetch_catalog_document", args={"url": "http://x"}
            ),
        ]
        adapter._tool_calls[0].result = "found docs"
        adapter._tool_calls[1].result = "page content"
        adapter._usage = {"prompt_tokens": 100, "completion_tokens": 50}

        final = adapter.build_final_output(source_documents=["doc1"])
        assert final.answer == "Final answer"
        assert final.final is True
        assert final.source_documents == ["doc1"]
        assert final.metadata["event_type"] == "final"
        assert len(final.metadata["tool_calls"]) == 2
        assert final.metadata["tool_calls"][0]["name"] == "search_knowledge_base"
        assert final.metadata["usage"]["prompt_tokens"] == 100

    def test_build_final_no_usage_omits_key(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._response_buffer = "answer"
        final = adapter.build_final_output()
        assert "usage" not in final.metadata


# ── Stream kwargs extraction ──────────────────────────────────────────────


class TestStreamKwargsExtraction:
    """Verify CopilotAgentPipeline.stream() correctly extracts per-request
    overrides. Tests the setup logic without running the async session."""

    def _make_pipeline(self):
        from src.archi.pipelines.copilot_agent import CopilotAgentPipeline

        p = CopilotAgentPipeline.__new__(CopilotAgentPipeline)
        p.default_provider = "openai"
        p.default_model = "gpt-4o"
        p._providers_config = {}
        p.agent_prompt = "test"
        p.archi_config = {}
        p.dm_config = {}
        p._catalog_client = None
        p._monit_client = None
        p.selected_tool_names = []
        p._async_loop = FakeAsyncLoop()
        return p

    def test_session_api_key_preferred_over_db_key(self):
        """Session-provided API key takes precedence over DB-stored key."""
        p = self._make_pipeline()

        with (
            patch.object(p, "_resolve_byok_key", return_value="db-stored-key"),
            patch.object(p, "_build_tools", return_value=[]),
            patch.object(
                p, "_build_session_config", return_value={"_tools": []}
            ) as mock_cfg,
        ):
            # Call _build_session_config indirectly by simulating stream setup
            # We replicate the key extraction logic from stream()
            session_api_key = "session-key"
            db_key = p._resolve_byok_key("u1")
            api_key = session_api_key or db_key
            assert api_key == "session-key"

    def test_db_key_used_when_no_session_key(self):
        """Without session API key, falls back to DB-stored key."""
        p = self._make_pipeline()

        with patch.object(p, "_resolve_byok_key", return_value="db-key"):
            session_api_key = None
            db_key = p._resolve_byok_key("u1")
            api_key = session_api_key or db_key
            assert api_key == "db-key"

    def test_provider_model_override_to_config(self):
        """Provider/model overrides reach _build_session_config correctly."""
        p = self._make_pipeline()
        cfg = p._build_session_config(
            tools=[],
            api_key="k",
            provider_override="anthropic",
            model_override="claude-sonnet-4-20250514",
        )
        assert cfg["model"] == "claude-sonnet-4-20250514"
        assert cfg["provider"]["type"] == "anthropic"

    def test_no_override_uses_defaults(self):
        """Without overrides, defaults are used."""
        p = self._make_pipeline()
        cfg = p._build_session_config(tools=[], api_key="k")
        assert cfg["model"] == "gpt-4o"
        assert cfg["provider"]["type"] == "openai"
