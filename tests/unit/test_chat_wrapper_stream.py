"""Unit tests for ChatWrapper.stream() event routing.

Tests the mapping from PipelineOutput events (as produced by
CopilotEventAdapter) through ChatWrapper.stream() to the NDJSON events
consumed by the frontend.  Uses a mock archi instance so no real
pipeline, vectorstore, or database is needed.

Regression tests for bug #9 (error event not handled).
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from src.archi.utils.output_dataclass import PipelineOutput

# ── Minimal context stub ─────────────────────────────────────────────────


@dataclass
class FakeChatContext:
    sender: str = "user"
    content: str = "Hello"
    conversation_id: int = 42
    history: list = None
    is_refresh: bool = False

    def __post_init__(self):
        if self.history is None:
            self.history = [("user", "Hello")]


# ── Helper to create a minimal ChatWrapper for testing ───────────────────


def _make_chat_wrapper(pipeline_outputs: List[PipelineOutput]):
    """Create a ChatWrapper-like object with .stream() that routes
    the given pipeline outputs through the full event-routing logic.

    We monkey-patch heavily to avoid needing Flask, Postgres, etc.
    """
    # Import ChatWrapper — but we can't instantiate it (needs full config).
    # Instead, test the event routing logic by extracting the core loop.
    # We'll build a simplified version that exercises the same code paths.

    # Actually, the simplest approach: create a mock that has the real
    # stream() method bound to it, with all dependencies mocked.
    from src.interfaces.chat_app.app import ChatWrapper

    wrapper = object.__new__(ChatWrapper)

    # Set up minimal required attributes
    wrapper.lock = MagicMock()
    wrapper.number_of_queries = 0
    wrapper.conn = None
    wrapper.cursor = None
    wrapper.config = {"name": "test"}
    wrapper.current_config_name = "test"
    wrapper.similarity_score_reference = 0.5
    wrapper.current_model_used = "test-model"
    wrapper.current_pipeline_used = "CopilotAgentPipeline"

    # Mock archi to yield our controlled outputs
    mock_archi = MagicMock()
    mock_archi.stream = MagicMock(return_value=iter(pipeline_outputs))
    mock_archi.pipeline_name = "CopilotAgentPipeline"
    mock_archi.pipeline = MagicMock()
    mock_archi.pipeline.supports_persisted_session_id = MagicMock(return_value=True)
    wrapper.archi = mock_archi

    # Mock all DB/context methods
    wrapper._prepare_chat_context = MagicMock(
        return_value=(FakeChatContext(), None)  # context, error_code
    )
    wrapper._resolve_config_name = MagicMock(return_value="test")
    wrapper.update_config = MagicMock()
    wrapper.create_agent_trace = MagicMock(return_value="trace-123")
    wrapper.update_agent_trace = MagicMock()
    wrapper._init_timestamps = MagicMock(return_value={})
    wrapper._create_provider_llm = MagicMock(return_value=None)
    wrapper.insert_timing = MagicMock()
    wrapper.get_pipeline_session_id = MagicMock(return_value=None)
    wrapper.set_pipeline_session_id = MagicMock()

    # Mock _finalize_result to return the output text and message IDs
    def _fake_finalize(
        result, *, context, server_received_msg_ts, timestamps, render_markdown=True
    ):
        return result.answer or "finalized", [1, 2]

    wrapper._finalize_result = _fake_finalize
    wrapper.insert_tool_calls_from_output = MagicMock()
    wrapper.get_top_sources = MagicMock(return_value=[])
    wrapper.format_links_markdown = MagicMock(return_value="")

    return wrapper


def _collect_events(wrapper, **kwargs) -> List[Dict[str, Any]]:
    """Run wrapper.stream() and collect all yielded events."""
    now = datetime.now(timezone.utc)
    defaults = dict(
        message=[("user", "Hello")],
        conversation_id=42,
        client_id="test-client",
        is_refresh=False,
        server_received_msg_ts=now,
        client_sent_msg_ts=now.timestamp(),
        client_timeout=30.0,
        config_name="test",
    )
    defaults.update(kwargs)
    return list(wrapper.stream(**defaults))


# ── Tests ─────────────────────────────────────────────────────────────────


class TestStreamTextEvents:
    """Verify text chunks are routed correctly."""

    def test_text_events_yield_chunks(self):
        outputs = [
            PipelineOutput(
                answer="Hello", metadata={"event_type": "text"}, final=False
            ),
            PipelineOutput(
                answer="Hello world", metadata={"event_type": "text"}, final=False
            ),
            PipelineOutput(
                answer="Hello world", metadata={"event_type": "final"}, final=True
            ),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper)

        chunks = [e for e in events if e.get("type") == "chunk"]
        assert len(chunks) == 2
        assert chunks[0]["content"] == "Hello"
        assert chunks[1]["content"] == "Hello world"
        assert chunks[0]["accumulated"] is True

    def test_empty_text_not_yielded(self):
        outputs = [
            PipelineOutput(answer="", metadata={"event_type": "text"}, final=False),
            PipelineOutput(
                answer="Hello", metadata={"event_type": "text"}, final=False
            ),
            PipelineOutput(
                answer="Hello", metadata={"event_type": "final"}, final=True
            ),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper)

        chunks = [e for e in events if e.get("type") == "chunk"]
        # Empty text should NOT yield a chunk
        assert len(chunks) == 1
        assert chunks[0]["content"] == "Hello"


class TestStreamToolEvents:
    """Verify tool lifecycle events pass through."""

    def test_tool_start_event(self):
        outputs = [
            PipelineOutput(
                answer="",
                metadata={
                    "event_type": "tool_start",
                    "tool_call_id": "tc-1",
                    "tool_name": "search_vectorstore_hybrid",
                    "tool_args": {"query": "test"},
                },
                final=False,
            ),
            PipelineOutput(
                answer="result", metadata={"event_type": "text"}, final=False
            ),
            PipelineOutput(
                answer="result", metadata={"event_type": "final"}, final=True
            ),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper)

        tool_starts = [e for e in events if e.get("type") == "tool_start"]
        assert len(tool_starts) == 1
        assert tool_starts[0]["tool_name"] == "search_vectorstore_hybrid"
        assert tool_starts[0]["tool_call_id"] == "tc-1"
        assert tool_starts[0]["tool_args"] == {"query": "test"}

    def test_tool_output_event(self):
        outputs = [
            PipelineOutput(
                answer="",
                metadata={
                    "event_type": "tool_output",
                    "tool_call_id": "tc-1",
                    "output": "Found 3 documents",
                },
                final=False,
            ),
            PipelineOutput(answer="done", metadata={"event_type": "final"}, final=True),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper)

        tool_outputs = [e for e in events if e.get("type") == "tool_output"]
        assert len(tool_outputs) == 1
        assert tool_outputs[0]["output"] == "Found 3 documents"
        assert tool_outputs[0]["tool_call_id"] == "tc-1"

    def test_tool_end_event(self):
        outputs = [
            PipelineOutput(
                answer="",
                metadata={
                    "event_type": "tool_end",
                    "tool_call_id": "tc-1",
                    "status": "success",
                    "duration_ms": 150,
                },
                final=False,
            ),
            PipelineOutput(answer="done", metadata={"event_type": "final"}, final=True),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper)

        tool_ends = [e for e in events if e.get("type") == "tool_end"]
        assert len(tool_ends) == 1
        assert tool_ends[0]["status"] == "success"

    def test_tool_output_truncation(self):
        long_output = "x" * 2000
        outputs = [
            PipelineOutput(
                answer="",
                metadata={
                    "event_type": "tool_output",
                    "tool_call_id": "tc-1",
                    "output": long_output,
                },
                final=False,
            ),
            PipelineOutput(answer="done", metadata={"event_type": "final"}, final=True),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper)

        tool_outputs = [e for e in events if e.get("type") == "tool_output"]
        assert len(tool_outputs) == 1
        assert tool_outputs[0]["truncated"] is True
        assert tool_outputs[0]["full_length"] == 2000
        assert len(tool_outputs[0]["output"]) <= 800


class TestStreamThinkingEvents:
    """Verify thinking lifecycle events."""

    def test_thinking_start_and_end(self):
        outputs = [
            PipelineOutput(
                answer="",
                metadata={"event_type": "thinking_start", "step_id": "s1"},
                final=False,
            ),
            PipelineOutput(
                answer="",
                metadata={
                    "event_type": "thinking_end",
                    "step_id": "s1",
                    "duration_ms": 200,
                    "thinking_content": "Let me analyze...",
                },
                final=False,
            ),
            PipelineOutput(
                answer="Result", metadata={"event_type": "text"}, final=False
            ),
            PipelineOutput(
                answer="Result", metadata={"event_type": "final"}, final=True
            ),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper)

        thinking_starts = [e for e in events if e.get("type") == "thinking_start"]
        thinking_ends = [e for e in events if e.get("type") == "thinking_end"]

        assert len(thinking_starts) == 1
        assert thinking_starts[0]["step_id"] == "s1"
        assert len(thinking_ends) == 1
        assert thinking_ends[0]["thinking_content"] == "Let me analyze..."
        assert thinking_ends[0]["step_id"] == "s1"


class TestStreamFinalEvent:
    """Verify the final event has all required fields."""

    def test_final_event_structure(self):
        outputs = [
            PipelineOutput(
                answer="Hello", metadata={"event_type": "text"}, final=False
            ),
            PipelineOutput(
                answer="Hello",
                metadata={
                    "event_type": "final",
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 50,
                        "total_tokens": 150,
                    },
                },
                final=True,
            ),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper)

        finals = [e for e in events if e.get("type") == "final"]
        assert len(finals) == 1
        final = finals[0]
        assert "response" in final
        assert "conversation_id" in final
        assert final["conversation_id"] == 42
        assert final["usage"] == {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
        assert "trace_id" in final

    def test_no_output_yields_error(self):
        """If pipeline yields nothing, stream should return an error."""
        wrapper = _make_chat_wrapper([])
        events = _collect_events(wrapper)

        errors = [e for e in events if e.get("type") == "error"]
        assert len(errors) == 1
        assert errors[0]["status"] == 500


class TestStreamErrorEvent:
    """Regression for bug #9: error events must propagate to client."""

    def test_error_event_yielded(self):
        outputs = [
            PipelineOutput(
                answer="partial", metadata={"event_type": "text"}, final=False
            ),
            PipelineOutput(
                answer="",
                metadata={"event_type": "error", "error": "SDK session crashed"},
                final=False,
            ),
            # After error, pipeline might still yield final
            PipelineOutput(
                answer="partial", metadata={"event_type": "final"}, final=True
            ),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper)

        errors = [e for e in events if e.get("type") == "error"]
        assert len(errors) == 1
        assert "SDK session crashed" in errors[0]["message"]

    def test_error_event_does_not_stop_stream(self):
        """An error event mid-stream should not prevent the final event."""
        outputs = [
            PipelineOutput(
                answer="",
                metadata={"event_type": "error", "error": "tool failed"},
                final=False,
            ),
            PipelineOutput(
                answer="recovered", metadata={"event_type": "text"}, final=False
            ),
            PipelineOutput(
                answer="recovered", metadata={"event_type": "final"}, final=True
            ),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper)

        finals = [e for e in events if e.get("type") == "final"]
        assert len(finals) == 1


class TestStreamUsageInTrace:
    """Verify usage data ends up in trace events."""

    def test_usage_appended_to_trace_events(self):
        outputs = [
            PipelineOutput(answer="Hi", metadata={"event_type": "text"}, final=False),
            PipelineOutput(
                answer="Hi",
                metadata={
                    "event_type": "final",
                    "usage": {
                        "prompt_tokens": 200,
                        "completion_tokens": 80,
                        "total_tokens": 280,
                    },
                },
                final=True,
            ),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper)

        # The update_agent_trace call should have received usage in trace_events
        assert wrapper.update_agent_trace.called
        call_kwargs = wrapper.update_agent_trace.call_args
        trace_events = call_kwargs.kwargs.get("events") or call_kwargs[1].get(
            "events", []
        )
        usage_events = [e for e in trace_events if e.get("type") == "usage"]
        assert len(usage_events) == 1
        assert usage_events[0]["prompt_tokens"] == 200
        assert usage_events[0]["completion_tokens"] == 80


class TestStreamContextPrepFailure:
    """Verify stream handles _prepare_chat_context errors."""

    def test_timeout_error(self):
        wrapper = _make_chat_wrapper([])
        wrapper._prepare_chat_context = MagicMock(return_value=(None, 408))

        events = _collect_events(wrapper)
        errors = [e for e in events if e.get("type") == "error"]
        assert len(errors) == 1
        assert errors[0]["status"] == 408
        assert "timeout" in errors[0]["message"]

    def test_conversation_not_found(self):
        wrapper = _make_chat_wrapper([])
        wrapper._prepare_chat_context = MagicMock(return_value=(None, 403))

        events = _collect_events(wrapper)
        errors = [e for e in events if e.get("type") == "error"]
        assert len(errors) == 1
        assert errors[0]["status"] == 403


class TestStreamToolStepsDisabled:
    """Verify include_tool_steps=False suppresses tool events."""

    def test_tool_events_suppressed(self):
        outputs = [
            PipelineOutput(
                answer="",
                metadata={
                    "event_type": "tool_start",
                    "tool_call_id": "tc-1",
                    "tool_name": "search_vectorstore_hybrid",
                    "tool_args": {},
                },
                final=False,
            ),
            PipelineOutput(
                answer="",
                metadata={
                    "event_type": "tool_output",
                    "tool_call_id": "tc-1",
                    "output": "data",
                },
                final=False,
            ),
            PipelineOutput(answer="done", metadata={"event_type": "text"}, final=False),
            PipelineOutput(answer="done", metadata={"event_type": "final"}, final=True),
        ]
        wrapper = _make_chat_wrapper(outputs)
        events = _collect_events(wrapper, include_tool_steps=False)

        tool_events = [
            e for e in events if e.get("type") in ("tool_start", "tool_output")
        ]
        assert tool_events == []

        chunks = [e for e in events if e.get("type") == "chunk"]
        assert len(chunks) == 1


class TestStreamProviderOverride:
    """Bug #15/#16: provider/model/api_key forwarded to archi.stream()."""

    def test_provider_model_passed_to_archi_stream(self):
        """When provider & model are set, they appear in archi.stream() kwargs."""
        outputs = [
            PipelineOutput(answer="ok", metadata={"event_type": "final"}, final=True),
        ]
        wrapper = _make_chat_wrapper(outputs)
        _collect_events(wrapper, provider="anthropic", model="claude-sonnet-4-20250514")

        # archi.stream() should have been called with provider and model kwargs
        call_kwargs = wrapper.archi.stream.call_args
        assert call_kwargs is not None
        # Check kwargs (may be passed as keyword args)
        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else {}
        assert kwargs.get("provider") == "anthropic"
        assert kwargs.get("model") == "claude-sonnet-4-20250514"

    def test_api_key_passed_to_archi_stream(self):
        """When provider_api_key is set, it appears in archi.stream() kwargs."""
        outputs = [
            PipelineOutput(answer="ok", metadata={"event_type": "final"}, final=True),
        ]
        wrapper = _make_chat_wrapper(outputs)
        _collect_events(
            wrapper, provider="openai", model="gpt-4o", provider_api_key="sk-user-key"
        )

        kwargs = wrapper.archi.stream.call_args.kwargs
        assert kwargs.get("provider_api_key") == "sk-user-key"

    def test_no_override_when_provider_not_set(self):
        """Without provider/model, they should not appear in archi.stream() kwargs."""
        outputs = [
            PipelineOutput(answer="ok", metadata={"event_type": "final"}, final=True),
        ]
        wrapper = _make_chat_wrapper(outputs)
        _collect_events(wrapper)

        kwargs = wrapper.archi.stream.call_args.kwargs
        assert "provider" not in kwargs
        assert "model" not in kwargs
        assert "provider_api_key" not in kwargs


class TestCopilotSessionPersistence:
    """Persist and reuse real Copilot SDK session IDs across turns."""

    def test_stored_session_id_forwarded_to_pipeline(self):
        outputs = [
            PipelineOutput(answer="ok", metadata={"event_type": "final"}, final=True),
        ]
        wrapper = _make_chat_wrapper(outputs)
        wrapper.get_pipeline_session_id.return_value = "sdk-session-123"

        _collect_events(wrapper)

        kwargs = wrapper.archi.stream.call_args.kwargs
        assert kwargs.get("pipeline_session_id") == "sdk-session-123"

    def test_final_output_persists_new_session_id(self):
        outputs = [
            PipelineOutput(
                answer="ok",
                metadata={
                    "event_type": "final",
                    "pipeline_session_id": "sdk-session-456",
                },
                final=True,
            ),
        ]
        wrapper = _make_chat_wrapper(outputs)
        wrapper.get_pipeline_session_id.return_value = None

        _collect_events(wrapper)

        wrapper.set_pipeline_session_id.assert_called_once_with(
            42,
            "test-client",
            "sdk-session-456",
            user_id=None,
        )
