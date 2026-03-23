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
    """Create a mock SDK event."""
    ev = MagicMock()
    ev.type = event_type
    for k, v in kwargs.items():
        setattr(ev, k, v)
    return ev


def _make_tool_use(*, id="tc-1", name="my_tool", arguments=None, result=None):
    tu = MagicMock()
    tu.id = id
    tu.name = name
    tu.arguments = arguments or {"q": "test"}
    tu.result = result
    return tu


# ── Tests ─────────────────────────────────────────────────────────────────

class TestTextAccumulation:
    """Decision 14: adapter must accumulate message deltas."""

    def test_message_deltas_accumulate(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        # Simulate two message_delta events via consume_session
        events = [
            _make_event("assistant.message_delta", content="Hello"),
            _make_event("assistant.message_delta", content=" world"),
            _make_event("session.idle", usage=None),
        ]

        async def fake_events():
            for e in events:
                yield e

        session = MagicMock()
        session.events = fake_events

        # Run consume_session
        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.consume_session(session))
        loop.close()

        # Drain queue
        outputs = []
        while True:
            item = adapter._queue.get_nowait()
            if item is adapter.__class__.__dict__.get("_SENTINEL") or not isinstance(item, PipelineOutput):
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
            _make_event("assistant.reasoning_delta", content="Let me think..."),
            _make_event("assistant.message_delta", content="Answer"),
            _make_event("session.idle", usage=None),
        ]

        async def fake_events():
            for e in events:
                yield e

        session = MagicMock()
        session.events = fake_events

        loop = asyncio.new_event_loop()
        loop.run_until_complete(adapter.consume_session(session))
        loop.close()

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

    @pytest.mark.asyncio
    async def test_pre_tool_use_emits_tool_start(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        tool_use = _make_tool_use()

        result = await adapter.on_pre_tool_use(tool_use)
        assert result == {"permissionDecision": "allow"}

        item = adapter._queue.get_nowait()
        assert isinstance(item, PipelineOutput)
        assert item.metadata["event_type"] == "tool_start"
        assert item.metadata["tool_name"] == "my_tool"
        assert item.metadata["tool_call_id"] == "tc-1"

    @pytest.mark.asyncio
    async def test_post_tool_use_emits_tool_output(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        # First call pre to register the tool
        tool_use = _make_tool_use()
        await adapter.on_pre_tool_use(tool_use)
        adapter._queue.get_nowait()  # discard tool_start

        # Now post
        tool_use.result = "42"
        await adapter.on_post_tool_use(tool_use)

        item = adapter._queue.get_nowait()
        assert isinstance(item, PipelineOutput)
        assert item.metadata["event_type"] == "tool_output"
        assert item.metadata["output"] == "42"

    @pytest.mark.asyncio
    async def test_tool_calls_recorded_for_metadata(self):
        """Decision 12: tool calls stored in metadata."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        tool_use = _make_tool_use(id="tc-99", name="search")
        await adapter.on_pre_tool_use(tool_use)
        tool_use.result = "found it"
        await adapter.on_post_tool_use(tool_use)

        assert len(adapter._tool_calls) == 1
        assert adapter._tool_calls[0].name == "search"
        assert adapter._tool_calls[0].result == "found it"

        final = adapter.build_final_output()
        tc = final.metadata["tool_calls"]
        assert len(tc) == 1
        assert tc[0]["name"] == "search"
        assert tc[0]["result"] == "found it"

    @pytest.mark.asyncio
    async def test_cancelled_denies_tool(self):
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._cancelled = True
        tool_use = _make_tool_use()
        result = await adapter.on_pre_tool_use(tool_use)
        assert result == {"permissionDecision": "deny"}


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
        usage = MagicMock()
        usage.prompt_tokens = None
        usage.promptTokens = 200
        usage.completion_tokens = None
        usage.completionTokens = 80
        usage.total_tokens = None
        usage.totalTokens = 280
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
