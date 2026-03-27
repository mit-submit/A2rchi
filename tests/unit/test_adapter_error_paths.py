"""Unit tests for CopilotEventAdapter error and concurrency paths.

Regression tests for bugs #7 (deadlock on session error),
#10 (queue poll_timeout hang), and cancellation propagation.
"""

import asyncio
import queue
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.archi.copilot_event_adapter import _SENTINEL, CopilotEventAdapter
from src.archi.utils.output_dataclass import PipelineOutput

# ── Helpers ───────────────────────────────────────────────────────────────


class FakeAsyncLoop:
    """Minimal stub for AsyncLoopThread."""

    def __init__(self):
        self._loop = None

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
    """Create a mock SDK event with proper enum and data."""
    from enum import Enum

    SessionEventType = Enum(
        "SessionEventType",
        {
            "ASSISTANT_MESSAGE_DELTA": "assistant.message_delta",
            "SESSION_ERROR": "session.error",
        },
    )
    _type_map = {
        "assistant.message_delta": SessionEventType.ASSISTANT_MESSAGE_DELTA,
        "session.error": SessionEventType.SESSION_ERROR,
    }
    ev = MagicMock()
    ev.type = _type_map.get(event_type, event_type)
    data = MagicMock()
    for k, v in kwargs.items():
        setattr(data, k, v)
    ev.data = data
    return ev


# ── Tests ─────────────────────────────────────────────────────────────────


class TestSessionErrorUnblocksQueue:
    """Regression for bug #7: _run_session raises; iter_outputs() must
    terminate instead of deadlocking."""

    def test_signal_done_after_error_unblocks(self):
        """If the async session throws and calls signal_done() in its
        finally block, iter_outputs() must drain and return."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        # Simulate: some text arrives, then error, then signal_done
        adapter._queue.put(
            PipelineOutput(
                answer="partial",
                metadata={"event_type": "text"},
                final=False,
            )
        )
        adapter._queue.put(
            PipelineOutput(
                answer="",
                metadata={"event_type": "error", "error": "session crashed"},
                final=False,
            )
        )
        adapter.signal_done()

        results = list(adapter.iter_outputs())
        assert len(results) == 2
        assert results[0].answer == "partial"
        assert results[1].metadata["event_type"] == "error"

    def test_error_event_via_session_on_handler(self):
        """session.error events log but don't crash the adapter."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        session = MagicMock()
        handler_ref = []
        session.on = lambda h: (handler_ref.append(h), lambda: None)[1]
        adapter.attach_to_session(session)

        error_event = _make_event("session.error", message="SDK blew up")
        handler_ref[0](error_event)

        # No PipelineOutput for session.error — it only logs.
        # The queue should still be empty.
        assert adapter._queue.empty()

    def test_no_sentinel_timeout_returns(self):
        """If signal_done() never fires, iter_outputs must return after
        poll_timeout (not hang forever). Regression for bug #10."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._queue.put(
            PipelineOutput(
                answer="ok",
                metadata={"event_type": "text"},
                final=False,
            )
        )
        # No signal_done — simulates async crash without cleanup

        start = time.monotonic()
        results = list(adapter.iter_outputs(poll_timeout=0.2))
        elapsed = time.monotonic() - start

        assert len(results) == 1
        assert results[0].answer == "ok"
        # Should have returned after ~0.2s, not minutes
        assert elapsed < 2.0

    def test_empty_queue_timeout_returns_nothing(self):
        """Completely empty queue with no sentinel should still return
        an empty list after poll_timeout, not hang."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        start = time.monotonic()
        results = list(adapter.iter_outputs(poll_timeout=0.1))
        elapsed = time.monotonic() - start

        assert results == []
        assert elapsed < 2.0


class TestCancellationPropagation:
    """Cancellation via GeneratorExit should disconnect the session."""

    def test_generator_exit_sets_cancelled(self):
        """When the consumer raises GeneratorExit, the adapter must set
        _cancelled = True and attempt session disconnect."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        # Mock a session with a disconnect method
        mock_session = MagicMock()

        async def fake_disconnect():
            pass

        mock_session.disconnect = MagicMock(return_value=fake_disconnect())
        adapter._session = mock_session

        # Put some items to iterate
        adapter._queue.put(
            PipelineOutput(
                answer="a",
                metadata={"event_type": "text"},
                final=False,
            )
        )
        adapter._queue.put(
            PipelineOutput(
                answer="b",
                metadata={"event_type": "text"},
                final=False,
            )
        )
        adapter._queue.put(_SENTINEL)

        gen = adapter.iter_outputs()
        # Consume first item, then close (simulates client disconnect)
        first = next(gen)
        assert first.answer == "a"
        gen.close()

        assert adapter._cancelled is True

    def test_cancelled_flag_suppresses_events(self):
        """After cancellation, new events pushed by the handler should
        be ignored."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        adapter._cancelled = True

        session = MagicMock()
        handler_ref = []
        session.on = lambda h: (handler_ref.append(h), lambda: None)[1]
        adapter.attach_to_session(session)

        # Fire an event after cancellation
        event = _make_event("assistant.message_delta", delta_content="ignored")
        handler_ref[0](event)

        # Queue should still be empty (event was suppressed)
        assert adapter._queue.empty()


class TestConcurrentAccess:
    """Verify adapter works correctly when producer and consumer run
    on different threads (the actual deployment pattern)."""

    def test_threaded_producer_consumer(self):
        """Producer pushes events from one thread, consumer drains
        iter_outputs() from another. Must not deadlock."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        results = []
        errors = []

        def producer():
            try:
                time.sleep(0.05)
                for i in range(5):
                    adapter._queue.put(
                        PipelineOutput(
                            answer=f"chunk-{i}",
                            metadata={"event_type": "text"},
                            final=False,
                        )
                    )
                    time.sleep(0.01)
                adapter.signal_done()
            except Exception as e:
                errors.append(e)

        def consumer():
            try:
                for output in adapter.iter_outputs(poll_timeout=5.0):
                    results.append(output.answer)
            except Exception as e:
                errors.append(e)

        t_prod = threading.Thread(target=producer)
        t_cons = threading.Thread(target=consumer)
        t_cons.start()
        t_prod.start()
        t_prod.join(timeout=5)
        t_cons.join(timeout=5)

        assert not errors, f"Thread errors: {errors}"
        assert results == [f"chunk-{i}" for i in range(5)]

    def test_threaded_producer_crashes(self):
        """Producer crashes without signal_done — consumer must still
        return after poll_timeout. Regression for bug #7."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        results = []

        def crashing_producer():
            adapter._queue.put(
                PipelineOutput(
                    answer="before crash",
                    metadata={"event_type": "text"},
                    final=False,
                )
            )
            # Crash — no signal_done()

        def consumer():
            for output in adapter.iter_outputs(poll_timeout=0.3):
                results.append(output.answer)

        t_prod = threading.Thread(target=crashing_producer)
        t_cons = threading.Thread(target=consumer)
        t_cons.start()
        t_prod.start()
        t_prod.join(timeout=2)
        t_cons.join(timeout=5)

        assert results == ["before crash"]


class TestAttachToSessionCorrectness:
    """Verify attach_to_session wires up the event handler correctly."""

    def test_session_on_called(self):
        """attach_to_session must call session.on() with a handler."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())
        session = MagicMock()
        calls = []
        session.on = lambda h: (calls.append(h), lambda: None)[1]

        adapter.attach_to_session(session)

        assert len(calls) == 1
        assert callable(calls[0])
        assert adapter._session is session

    def test_multiple_attach_replaces_session(self):
        """Re-attaching to a new session should update _session reference."""
        adapter = CopilotEventAdapter(FakeAsyncLoop())

        session1 = MagicMock()
        session1.on = lambda h: (None, lambda: None)[1]
        adapter.attach_to_session(session1)
        assert adapter._session is session1

        session2 = MagicMock()
        session2.on = lambda h: (None, lambda: None)[1]
        adapter.attach_to_session(session2)
        assert adapter._session is session2
