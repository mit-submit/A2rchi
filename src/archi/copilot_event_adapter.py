"""Translate Copilot SDK session events into PipelineOutput objects.

The adapter subscribes to a Copilot SDK session's async event stream and
pushes ``PipelineOutput`` objects into a thread-safe ``queue.Queue``.  A
synchronous generator (``iter_outputs()``) drains the queue on the Flask
thread so ChatWrapper.stream() can yield them unchanged.

Key behaviours (see design.md decisions 3, 14, 18, 20):

* **Text accumulation** — SDK ``message_delta`` events are accumulated
  into ``_response_buffer``; each yielded PipelineOutput contains the
  full accumulated text (``accumulated: true`` contract).
* **Thinking state machine** — SDK ``reasoning_delta`` events have no
  explicit start/end signals.  The adapter tracks ``_in_thinking`` and
  emits paired ``thinking_start`` / ``thinking_end`` events with
  matching ``step_id``.
* **Tool lifecycle via hooks** — Tool events come through
  ``on_pre_tool_use`` / ``on_post_tool_use`` hooks, not the event
  stream.  The hooks push ``tool_start`` / ``tool_output`` into the
  shared queue.
* **Cancellation cleanup** — ``iter_outputs()``'s ``finally`` block
  calls ``session.disconnect()`` via the async loop (decision 18).
* **Usage metadata** — Populated from the SDK session's idle /
  final event (decision 20).
"""

from __future__ import annotations

import queue
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from src.archi.utils.async_loop import AsyncLoopThread
from src.archi.utils.output_dataclass import PipelineOutput
from src.utils.logging import get_logger

logger = get_logger(__name__)

_SENTINEL = object()  # Signals end-of-stream to the queue consumer


@dataclass
class _ToolCallRecord:
    """Track a single tool invocation for metadata storage (decision 12)."""
    id: str
    name: str
    args: Dict[str, Any]
    result: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CopilotEventAdapter:
    """Bridges async Copilot SDK events to sync PipelineOutput iteration.

    Lifecycle::

        adapter = CopilotEventAdapter(async_loop)
        # Pass adapter.on_pre_tool_use / adapter.on_post_tool_use as
        # session hooks when creating the Copilot SDK session.
        # Then call adapter.consume_session(session) from the async loop.
        for output in adapter.iter_outputs():
            yield output  # PipelineOutput
    """

    def __init__(self, async_loop: AsyncLoopThread) -> None:
        self._async_loop = async_loop
        self._queue: queue.Queue = queue.Queue()

        # Text accumulation (decision 14)
        self._response_buffer: str = ""

        # Thinking state machine (decision 3)
        self._in_thinking: bool = False
        self._thinking_step_id: Optional[str] = None
        self._thinking_start_time: Optional[float] = None
        self._thinking_buffer: str = ""

        # Tool tracking (decision 12)
        self._tool_calls: List[_ToolCallRecord] = []
        self._active_tools: Dict[str, _ToolCallRecord] = {}

        # Usage metadata (decision 20)
        self._usage: Optional[Dict[str, Any]] = None

        # Session reference for cleanup
        self._session: Any = None

        # Cancellation flag
        self._cancelled: bool = False

    # ── Hook callbacks (passed to SDK session creation) ───────────────

    def on_pre_tool_use(self, hook_input, context=None):
        """Fires before tool permission check (decision 3).

        Called by the SDK as ``handler(hook_input_dict, context_dict)``.
        ``hook_input`` is a dict with keys: ``toolName``, ``toolArgs``,
        ``timestamp``, ``cwd``.

        Emits ``tool_start``.
        """
        tool_call_id = str(uuid.uuid4())
        tool_name = hook_input.get("toolName", "unknown") if isinstance(hook_input, dict) else getattr(hook_input, "toolName", "unknown")
        tool_args = hook_input.get("toolArgs", {}) if isinstance(hook_input, dict) else getattr(hook_input, "toolArgs", {})

        record = _ToolCallRecord(id=tool_call_id, name=tool_name, args=tool_args)
        self._active_tools[tool_call_id] = record
        self._tool_calls.append(record)

        # End thinking if active (tool invocation breaks thinking)
        self._end_thinking_if_active()

        self._queue.put(PipelineOutput(
            answer="",
            metadata={
                "event_type": "tool_start",
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "tool_args": tool_args,
            },
            final=False,
        ))

    def on_post_tool_use(self, hook_input, context=None):
        """Fires after tool execution completes (decision 3).

        Called by the SDK as ``handler(hook_input_dict, context_dict)``.
        ``hook_input`` is a dict with keys: ``toolName``, ``toolArgs``,
        ``toolResult``, ``timestamp``, ``cwd``.

        Emits ``tool_output`` and records the result for metadata storage.
        """
        tool_name = hook_input.get("toolName", "unknown") if isinstance(hook_input, dict) else getattr(hook_input, "toolName", "unknown")
        result = hook_input.get("toolResult", None) if isinstance(hook_input, dict) else getattr(hook_input, "toolResult", None)
        result_str = str(result) if result is not None else ""

        # Match to active tool record by name (SDK hook inputs don't carry a call ID)
        tool_call_id = ""
        matched_record = None
        for tid, record in list(self._active_tools.items()):
            if record.name == tool_name:
                tool_call_id = tid
                matched_record = record
                break
        if matched_record is not None:
            matched_record.result = result_str
            self._active_tools.pop(tool_call_id, None)
        else:
            # No matching pre-hook (shouldn't happen, but be defensive)
            tool_call_id = str(uuid.uuid4())

        self._queue.put(PipelineOutput(
            answer="",
            metadata={
                "event_type": "tool_output",
                "tool_call_id": tool_call_id,
                "output": result_str,
            },
            final=False,
        ))

    # ── Event-based session consumer ─────────────────────────────────

    def attach_to_session(self, session) -> None:
        """Register an event handler on the session via ``session.on()``.

        Events are dispatched by the SDK; this method returns immediately.
        Call ``signal_done()`` after ``send_and_wait()`` returns to push
        the sentinel and unblock ``iter_outputs()``.
        """
        self._session = session

        def _on_event(event):
            if self._cancelled:
                return

            # Compare by value string for compatibility with both real and
            # mock SessionEventType enums.
            raw_type = event.type
            event_type = raw_type.value if hasattr(raw_type, "value") else str(raw_type)
            data = event.data

            if event_type in ("assistant.streaming_delta", "assistant.message_delta"):
                delta = getattr(data, "delta_content", "") or ""
                if delta:
                    self._end_thinking_if_active()
                    self._response_buffer += delta
                    self._queue.put(PipelineOutput(
                        answer=self._response_buffer,
                        metadata={"event_type": "text"},
                        final=False,
                    ))

            elif event_type == "assistant.reasoning_delta":
                delta = getattr(data, "delta_content", "") or getattr(data, "reasoning_text", "") or ""
                if delta:
                    if not self._in_thinking:
                        self._start_thinking()
                    self._thinking_buffer += delta

            elif event_type == "assistant.message":
                content = getattr(data, "content", "") or ""
                if content:
                    self._end_thinking_if_active()
                    self._response_buffer = content
                    self._queue.put(PipelineOutput(
                        answer=self._response_buffer,
                        metadata={"event_type": "text"},
                        final=False,
                    ))

            elif event_type == "assistant.reasoning":
                content = getattr(data, "content", "") or getattr(data, "reasoning_text", "") or ""
                if content:
                    self._thinking_buffer = content
                self._end_thinking_if_active()

            elif event_type == "assistant.turn_end":
                self._end_thinking_if_active()

            elif event_type == "session.idle":
                self._end_thinking_if_active()

            elif event_type == "assistant.usage":
                self._capture_usage(data)

            elif event_type == "session.error":
                error_msg = getattr(data, "message", "") or ""
                logger.error("Copilot SDK session error: %s", error_msg)

        session.on(_on_event)

    def signal_done(self) -> None:
        """Push the sentinel to unblock ``iter_outputs()``.

        Called after ``send_and_wait()`` completes.
        """
        self._end_thinking_if_active()
        self._queue.put(_SENTINEL)

    # ── Sync generator (consumed by Flask thread) ─────────────────────

    def iter_outputs(self, *, poll_timeout: float = 180.0) -> Iterator[PipelineOutput]:
        """Yield PipelineOutput objects until the session stream ends.

        On GeneratorExit (stream cancelled), disconnects the SDK session.
        Uses a poll timeout to prevent indefinite blocking if the async
        session crashes without calling ``signal_done()``.
        """
        try:
            while True:
                try:
                    item = self._queue.get(timeout=poll_timeout)
                except queue.Empty:
                    logger.warning("Adapter queue timed out after %.0fs — session may have crashed", poll_timeout)
                    break
                if item is _SENTINEL:
                    break
                yield item
        except GeneratorExit:
            self._cancelled = True
            if self._session is not None:
                try:
                    self._async_loop.run(self._session.disconnect(), timeout=5.0)
                except Exception:
                    logger.debug("Error disconnecting session on cancel", exc_info=True)
            raise
        finally:
            self._cancelled = True

    def build_final_output(
        self,
        *,
        source_documents: Optional[list] = None,
        retriever_scores: Optional[list] = None,
    ) -> PipelineOutput:
        """Build the terminal PipelineOutput with accumulated state.

        Called after ``iter_outputs()`` is exhausted, before the final
        event is yielded to ChatWrapper.
        """
        metadata: Dict[str, Any] = {"event_type": "final"}

        if self._usage is not None:
            metadata["usage"] = self._usage

        if self._tool_calls:
            metadata["tool_calls"] = [
                {
                    "id": tc.id,
                    "name": tc.name,
                    "args": tc.args,
                    "result": tc.result or "",
                    "created_at": tc.created_at,
                }
                for tc in self._tool_calls
            ]

        if retriever_scores:
            metadata["retriever_scores"] = retriever_scores

        return PipelineOutput(
            answer=self._response_buffer,
            source_documents=source_documents or [],
            metadata=metadata,
            final=True,
        )

    # ── Internal helpers ──────────────────────────────────────────────

    def _start_thinking(self) -> None:
        self._in_thinking = True
        self._thinking_step_id = str(uuid.uuid4())
        self._thinking_start_time = time.time()
        self._thinking_buffer = ""
        self._queue.put(PipelineOutput(
            answer="",
            metadata={
                "event_type": "thinking_start",
                "step_id": self._thinking_step_id,
            },
            final=False,
        ))

    def _end_thinking_if_active(self) -> None:
        if not self._in_thinking:
            return
        duration_ms = (
            int((time.time() - self._thinking_start_time) * 1000)
            if self._thinking_start_time
            else 0
        )
        self._queue.put(PipelineOutput(
            answer="",
            metadata={
                "event_type": "thinking_end",
                "step_id": self._thinking_step_id or "",
                "duration_ms": duration_ms,
                "thinking_content": self._thinking_buffer,
            },
            final=False,
        ))
        self._in_thinking = False
        self._thinking_step_id = None
        self._thinking_start_time = None
        self._thinking_buffer = ""

    def _capture_usage(self, usage) -> None:
        """Normalize SDK usage/data object to the frontend-expected dict."""
        if isinstance(usage, dict):
            raw = usage
        else:
            # SDK Data object from ASSISTANT_USAGE event
            input_tokens = (
                getattr(usage, "input_tokens", None)
                or getattr(usage, "prompt_tokens", None)
                or 0
            )
            output_tokens = (
                getattr(usage, "output_tokens", None)
                or getattr(usage, "completion_tokens", None)
                or 0
            )
            raw = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": (input_tokens or 0) + (output_tokens or 0),
            }

        self._usage = {
            "prompt_tokens": raw.get("prompt_tokens", 0),
            "completion_tokens": raw.get("completion_tokens", 0),
            "total_tokens": raw.get("total_tokens", 0),
            "context_window": raw.get("context_window"),
        }
