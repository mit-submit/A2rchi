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
            "TOOL_EXECUTION_START": "tool.execution_start",
            "TOOL_EXECUTION_COMPLETE": "tool.execution_complete",
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
        "tool.execution_start": SessionEventType.TOOL_EXECUTION_START,
        "tool.execution_complete": SessionEventType.TOOL_EXECUTION_COMPLETE,
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


class TestToolStreamingEvents:
    """Tool events via streaming events (tool.execution_start / tool.execution_complete)."""

    def test_tool_execution_start_emits_tool_start(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        events = [
            _make_event(
                "tool.execution_start",
                tool_call_id="tc-123",
                tool_name="my_tool",
                arguments={"q": "test"},
            ),
        ]
        _fire_events(adapter, events)

        outputs = []
        while not adapter._queue.empty():
            item = adapter._queue.get_nowait()
            if isinstance(item, PipelineOutput):
                outputs.append(item)

        tool_starts = [o for o in outputs if o.metadata.get("event_type") == "tool_start"]
        assert len(tool_starts) == 1
        assert tool_starts[0].metadata["tool_call_id"] == "tc-123"
        assert tool_starts[0].metadata["tool_name"] == "my_tool"
        assert tool_starts[0].metadata["tool_args"] == {"q": "test"}

    def test_tool_execution_complete_emits_tool_output(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        events = [
            _make_event(
                "tool.execution_start",
                tool_call_id="tc-456",
                tool_name="search",
                arguments={"q": "hello"},
            ),
            _make_event(
                "tool.execution_complete",
                tool_call_id="tc-456",
                result="found it",
            ),
        ]
        _fire_events(adapter, events)

        outputs = []
        while not adapter._queue.empty():
            item = adapter._queue.get_nowait()
            if isinstance(item, PipelineOutput):
                outputs.append(item)

        tool_outputs = [o for o in outputs if o.metadata.get("event_type") == "tool_output"]
        assert len(tool_outputs) == 1
        assert tool_outputs[0].metadata["tool_call_id"] == "tc-456"
        assert tool_outputs[0].metadata["output"] == "found it"

    def test_tool_calls_recorded_for_metadata(self):
        """Decision 12: tool calls stored in metadata."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        events = [
            _make_event(
                "tool.execution_start",
                tool_call_id="tc-789",
                tool_name="search",
                arguments={"q": "test"},
            ),
            _make_event(
                "tool.execution_complete",
                tool_call_id="tc-789",
                result="found it",
            ),
        ]
        _fire_events(adapter, events)

        assert len(adapter._tool_calls) == 1
        assert adapter._tool_calls[0].name == "search"
        assert adapter._tool_calls[0].result == "found it"
        assert adapter._tool_calls[0].id == "tc-789"

        final = adapter.build_final_output()
        tc = final.metadata["tool_calls"]
        assert len(tc) == 1
        assert tc[0]["name"] == "search"
        assert tc[0]["result"] == "found it"
        assert tc[0]["id"] == "tc-789"

    def test_tool_call_id_correlation(self):
        """Start and complete events should share the same native toolCallId."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        events = [
            _make_event(
                "tool.execution_start",
                tool_call_id="tc-corr-1",
                tool_name="search",
                arguments={},
            ),
            _make_event(
                "tool.execution_complete",
                tool_call_id="tc-corr-1",
                result="ok",
            ),
        ]
        _fire_events(adapter, events)

        outputs = []
        while not adapter._queue.empty():
            item = adapter._queue.get_nowait()
            if isinstance(item, PipelineOutput):
                outputs.append(item)

        start_ids = [o.metadata["tool_call_id"] for o in outputs if o.metadata.get("event_type") == "tool_start"]
        end_ids = [o.metadata["tool_call_id"] for o in outputs if o.metadata.get("event_type") == "tool_output"]
        assert start_ids == ["tc-corr-1"]
        assert end_ids == ["tc-corr-1"]

    def test_multiple_concurrent_tools(self):
        """Multiple tools running concurrently should be tracked independently."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        events = [
            _make_event("tool.execution_start", tool_call_id="tc-a", tool_name="search", arguments={"q": "a"}),
            _make_event("tool.execution_start", tool_call_id="tc-b", tool_name="fetch", arguments={"url": "b"}),
            _make_event("tool.execution_complete", tool_call_id="tc-b", result="result-b"),
            _make_event("tool.execution_complete", tool_call_id="tc-a", result="result-a"),
        ]
        _fire_events(adapter, events)

        assert len(adapter._tool_calls) == 2
        assert adapter._tool_calls[0].id == "tc-a"
        assert adapter._tool_calls[1].id == "tc-b"
        # Results are matched by ID, not order
        assert adapter._tool_calls[0].result == "result-a"
        assert adapter._tool_calls[1].result == "result-b"

    def test_tool_start_ends_thinking(self):
        """Tool invocation should end active thinking state."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        events = [
            _make_event("assistant.reasoning_delta", delta_content="Let me think..."),
            _make_event("tool.execution_start", tool_call_id="tc-x", tool_name="search", arguments={}),
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
        # thinking_end comes before tool_start
        thinking_end_idx = event_types.index("thinking_end")
        tool_start_idx = event_types.index("tool_start")
        assert thinking_end_idx < tool_start_idx


    def test_orphan_tool_complete_logs_warning(self, caplog):
        """tool.execution_complete without matching start logs a warning."""
        import logging
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        events = [
            _make_event(
                "tool.execution_complete",
                tool_call_id="tc-orphan",
                result="dangling result",
            ),
        ]
        with caplog.at_level(logging.WARNING):
            _fire_events(adapter, events)

        assert "unknown tool_call_id=tc-orphan" in caplog.text
        # Still emits tool_output and tool_end events so the UI doesn't hang
        outputs = []
        while not adapter._queue.empty():
            item = adapter._queue.get_nowait()
            if isinstance(item, PipelineOutput):
                outputs.append(item)
        event_types = [o.metadata.get("event_type") for o in outputs]
        assert "tool_output" in event_types
        assert "tool_end" in event_types


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


class TestSignalDoneUsageWarning:
    """Bug fix: signal_done logs a warning when no usage data received."""

    def test_warning_when_no_usage(self, caplog):
        import logging
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        assert adapter._usage is None
        with caplog.at_level(logging.WARNING):
            adapter.signal_done()
        assert "No usage data received" in caplog.text

    def test_no_warning_when_usage_present(self, caplog):
        import logging
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._usage = {"prompt_tokens": 10, "completion_tokens": 5}
        with caplog.at_level(logging.WARNING):
            adapter.signal_done()
        assert "No usage data received" not in caplog.text
