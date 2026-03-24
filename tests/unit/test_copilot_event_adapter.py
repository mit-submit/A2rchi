"""Unit tests for CopilotEventAdapter.

Tests the event→PipelineOutput translation, thinking state machine,
tool lifecycle via hooks, text accumulation, and cancellation.
"""

import asyncio
import queue
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.archi.copilot_event_adapter import CopilotEventAdapter, _ToolCallRecord
from src.archi.utils.output_dataclass import PipelineOutput


# ── Helpers ───────────────────────────────────────────────────────────────

class FakeAsyncLoop:
    """Minimal stub for AsyncLoopThread used in tests."""

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


def _make_event(event_type, **kwargs):
    """Create a mock SDK event with proper type enum and data object."""
    try:
        from copilot.generated.session_events import SessionEventType
    except ImportError:
        # SDK not installed locally — use a mock enum that matches by value
        from enum import Enum
        SessionEventType = Enum("SessionEventType", {
            "ASSISTANT_MESSAGE_DELTA": "assistant.message_delta",
            "ASSISTANT_STREAMING_DELTA": "assistant.streaming_delta",
            "ASSISTANT_REASONING_DELTA": "assistant.reasoning_delta",
            "ASSISTANT_MESSAGE": "assistant.message",
            "ASSISTANT_REASONING": "assistant.reasoning",
            "ASSISTANT_TURN_END": "assistant.turn_end",
            "ASSISTANT_USAGE": "assistant.usage",
            "SESSION_IDLE": "session.idle",
            "SESSION_ERROR": "session.error",
        })

    _type_map = {
        "assistant.message_delta": SessionEventType.ASSISTANT_MESSAGE_DELTA,
        "assistant.streaming_delta": SessionEventType.ASSISTANT_STREAMING_DELTA,
        "assistant.reasoning_delta": SessionEventType.ASSISTANT_REASONING_DELTA,
        "assistant.message": SessionEventType.ASSISTANT_MESSAGE,
        "assistant.reasoning": SessionEventType.ASSISTANT_REASONING,
        "assistant.turn_end": SessionEventType.ASSISTANT_TURN_END,
        "assistant.usage": SessionEventType.ASSISTANT_USAGE,
        "session.idle": SessionEventType.SESSION_IDLE,
        "session.error": SessionEventType.SESSION_ERROR,
    }
    ev = MagicMock()
    ev.type = _type_map.get(event_type, event_type)

    # Build data object with the specified attributes
    data = MagicMock()
    for k, v in kwargs.items():
        setattr(data, k, v)
    ev.data = data
    return ev


def _fire_events(adapter, events):
    """Fire events through the adapter's registered event handler."""
    # Get the handler that was registered via session.on()
    session = MagicMock()
    handler_ref = []

    def fake_on(handler):
        handler_ref.append(handler)
        return lambda: None

    session.on = fake_on
    adapter.attach_to_session(session)
    assert handler_ref, "No handler registered via session.on()"

    handler = handler_ref[0]
    for event in events:
        handler(event)
    adapter.signal_done()


def _make_tool_use(*, name="my_tool", args=None, result=None):
    """Create a hook input dict matching the SDK's PreToolUseHookInput /
    PostToolUseHookInput TypedDict format."""
    d = {
        "toolName": name,
        "toolArgs": args or {"q": "test"},
        "timestamp": 1700000000,
        "cwd": "/tmp",
    }
    if result is not None:
        d["toolResult"] = result
    return d


# ── Tests ─────────────────────────────────────────────────────────────────

class TestTextAccumulation:
    """Decision 14: adapter must accumulate message deltas."""

    def test_message_deltas_accumulate(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        events = [
            _make_event("assistant.message_delta", delta_content="Hello"),
            _make_event("assistant.message_delta", delta_content=" world"),
        ]

        _fire_events(adapter, events)

        # Drain queue
        outputs = []
        while True:
            item = adapter._queue.get_nowait()
            if not isinstance(item, PipelineOutput):
                break
            outputs.append(item)

        # First delta yields "Hello", second yields "Hello world"
        text_outputs = [o for o in outputs if o.metadata.get("event_type") == "text"]
        assert len(text_outputs) == 2
        assert text_outputs[0].answer == "Hello"
        assert text_outputs[1].answer == "Hello world"

    def test_final_output_has_full_text(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._response_buffer = "Complete answer"

        final = adapter.build_final_output()
        assert final.answer == "Complete answer"
        assert final.final is True
        assert final.metadata["event_type"] == "final"


class TestThinkingStateMachine:
    """Decision 3: paired thinking_start/thinking_end with step_id."""

    def test_reasoning_delta_starts_thinking(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        events = [
            _make_event("assistant.reasoning_delta", delta_content="Let me think..."),
            _make_event("assistant.message_delta", delta_content="Answer"),
        ]

        _fire_events(adapter, events)

        outputs = []
        while not adapter._queue.empty():
            item = adapter._queue.get_nowait()
            if isinstance(item, PipelineOutput):
                outputs.append(item)

        event_types = [o.metadata.get("event_type") for o in outputs]
        assert "thinking_start" in event_types
        assert "thinking_end" in event_types

        # thinking_end should contain the thinking content
        thinking_end = [o for o in outputs if o.metadata.get("event_type") == "thinking_end"][0]
        assert "Let me think..." in thinking_end.metadata.get("thinking_content", "")

        # thinking_start and thinking_end share the same step_id
        thinking_start = [o for o in outputs if o.metadata.get("event_type") == "thinking_start"][0]
        assert thinking_start.metadata["step_id"] == thinking_end.metadata["step_id"]


class TestToolHooks:
    """Decision 3: tool events via on_pre_tool_use / on_post_tool_use."""

    def test_pre_tool_use_emits_tool_start(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        hook_input = _make_tool_use()

        adapter.on_pre_tool_use(hook_input, {"session_id": "s1"})

        item = adapter._queue.get_nowait()
        assert isinstance(item, PipelineOutput)
        assert item.metadata["event_type"] == "tool_start"
        assert item.metadata["tool_name"] == "my_tool"
        assert item.metadata["tool_call_id"]  # UUID, not deterministic

    def test_post_tool_use_emits_tool_output(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        # First call pre to register the tool
        pre_input = _make_tool_use()
        adapter.on_pre_tool_use(pre_input, {"session_id": "s1"})
        adapter._queue.get_nowait()  # discard tool_start

        # Now post
        post_input = _make_tool_use(result="42")
        adapter.on_post_tool_use(post_input, {"session_id": "s1"})

        item = adapter._queue.get_nowait()
        assert isinstance(item, PipelineOutput)
        assert item.metadata["event_type"] == "tool_output"
        assert item.metadata["output"] == "42"

    def test_tool_calls_recorded_for_metadata(self):
        """Decision 12: tool calls stored in metadata."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        pre_input = _make_tool_use(name="search")
        adapter.on_pre_tool_use(pre_input, {"session_id": "s1"})
        post_input = _make_tool_use(name="search", result="found it")
        adapter.on_post_tool_use(post_input, {"session_id": "s1"})

        assert len(adapter._tool_calls) == 1
        assert adapter._tool_calls[0].name == "search"
        assert adapter._tool_calls[0].result == "found it"

        final = adapter.build_final_output()
        tc = final.metadata["tool_calls"]
        assert len(tc) == 1
        assert tc[0]["name"] == "search"
        assert tc[0]["result"] == "found it"

    def test_cancelled_pre_tool_use_still_records(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._cancelled = True
        hook_input = _make_tool_use()
        adapter.on_pre_tool_use(hook_input, {"session_id": "s1"})
        # Tool is still recorded even when cancelled
        assert len(adapter._tool_calls) == 1

    def test_hooks_accept_sdk_calling_convention(self):
        """SDK calls hooks as handler(input_dict, context_dict) — verify
        both positional args are accepted without error."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        pre_input = {"toolName": "run_query", "toolArgs": {"sql": "SELECT 1"}, "timestamp": 1, "cwd": "/"}
        context = {"session_id": "sess-123"}

        # Must not raise TypeError
        adapter.on_pre_tool_use(pre_input, context)
        item = adapter._queue.get_nowait()
        assert item.metadata["tool_name"] == "run_query"
        assert item.metadata["tool_args"] == {"sql": "SELECT 1"}

        post_input = {"toolName": "run_query", "toolArgs": {"sql": "SELECT 1"}, "toolResult": "1 row", "timestamp": 2, "cwd": "/"}
        adapter.on_post_tool_use(post_input, context)
        item = adapter._queue.get_nowait()
        assert item.metadata["output"] == "1 row"

    def test_pre_post_tool_call_id_match(self):
        """Pre and post hooks for the same tool name should share a call ID."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter.on_pre_tool_use({"toolName": "search", "toolArgs": {}, "timestamp": 1, "cwd": "/"}, {})
        pre_item = adapter._queue.get_nowait()
        pre_call_id = pre_item.metadata["tool_call_id"]

        adapter.on_post_tool_use({"toolName": "search", "toolArgs": {}, "toolResult": "ok", "timestamp": 2, "cwd": "/"}, {})
        post_item = adapter._queue.get_nowait()
        post_call_id = post_item.metadata["tool_call_id"]

        assert pre_call_id == post_call_id
        assert pre_call_id  # non-empty


class TestUsageCapture:
    """Decision 20: usage metadata normalization."""

    def test_capture_usage_dict(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._capture_usage({
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        })
        assert adapter._usage["prompt_tokens"] == 100
        assert adapter._usage["completion_tokens"] == 50
        assert adapter._usage["total_tokens"] == 150

    def test_capture_usage_object_camelcase(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        usage = MagicMock(spec=[])
        usage.input_tokens = 200
        usage.output_tokens = 80
        adapter._capture_usage(usage)
        assert adapter._usage["prompt_tokens"] == 200
        assert adapter._usage["completion_tokens"] == 80

    def test_usage_in_final_output(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._capture_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        final = adapter.build_final_output()
        assert final.metadata["usage"]["total_tokens"] == 15


class TestIterOutputs:
    """Test the sync generator bridge."""

    def test_iter_outputs_drains_queue(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._queue.put(PipelineOutput(answer="a", metadata={"event_type": "text"}, final=False))
        adapter._queue.put(PipelineOutput(answer="b", metadata={"event_type": "text"}, final=False))
        from src.archi.copilot_event_adapter import _SENTINEL
        adapter._queue.put(_SENTINEL)

        results = list(adapter.iter_outputs())
        assert len(results) == 2
        assert results[0].answer == "a"
        assert results[1].answer == "b"


class TestBuildFinalOutput:
    """Test final output construction."""

    def test_source_documents_included(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._response_buffer = "answer"

        doc = MagicMock()
        doc.page_content = "some content"
        final = adapter.build_final_output(
            source_documents=[doc],
            retriever_scores=[0.95],
        )
        assert len(final.source_documents) == 1
        assert final.metadata["retriever_scores"] == [0.95]


class TestIterOutputsTimeout:
    """Ensure iter_outputs doesn't block forever if signal_done is never called."""

    def test_queue_timeout_unblocks(self):
        """If signal_done() is never called, iter_outputs should still
        return after poll_timeout rather than hanging forever."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._queue.put(PipelineOutput(answer="ok", metadata={"event_type": "text"}, final=False))
        # No sentinel pushed — simulates async session crash

        results = list(adapter.iter_outputs(poll_timeout=0.1))
        assert len(results) == 1
        assert results[0].answer == "ok"
