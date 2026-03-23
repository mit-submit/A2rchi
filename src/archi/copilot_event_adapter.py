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

    async def on_pre_tool_use(self, tool_use):
        """Fires before tool permission check (decision 3).

        Returns ``permissionDecision: "allow"`` and emits ``tool_start``.
        """
        tool_call_id = getattr(tool_use, "id", None) or str(uuid.uuid4())
        tool_name = getattr(tool_use, "name", "unknown")
        tool_args = getattr(tool_use, "arguments", {}) or {}

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

        if self._cancelled:
            return {"permissionDecision": "deny"}
        return {"permissionDecision": "allow"}

    async def on_post_tool_use(self, tool_use):
        """Fires after tool execution completes (decision 3).

        Emits ``tool_output`` and records the result for metadata storage.
        """
        tool_call_id = getattr(tool_use, "id", None) or ""
        result = getattr(tool_use, "result", None)
        result_str = str(result) if result is not None else ""

        # Update the record with the result
        record = self._active_tools.pop(tool_call_id, None)
        if record is not None:
            record.result = result_str

        self._queue.put(PipelineOutput(
            answer="",
            metadata={
                "event_type": "tool_output",
                "tool_call_id": tool_call_id,
                "output": result_str,
            },
            final=False,
        ))

    # ── Async event consumer (runs on AsyncLoopThread) ────────────────

    async def consume_session(self, session) -> None:
        """Subscribe to session events and push PipelineOutputs into the queue.

        Called from the async loop.  When the session ends (or errors),
        the sentinel is pushed so ``iter_outputs()`` terminates.
        """
        self._session = session
        try:
            async for event in session.events():
                if self._cancelled:
                    break

                event_type = getattr(event, "type", None) or ""

                if event_type == "assistant.message_delta":
                    delta = getattr(event, "content", "") or ""
                    if delta:
                        self._end_thinking_if_active()
                        self._response_buffer += delta
                        self._queue.put(PipelineOutput(
                            answer=self._response_buffer,
                            metadata={"event_type": "text"},
                            final=False,
                        ))

                elif event_type == "assistant.reasoning_delta":
                    delta = getattr(event, "content", "") or ""
                    if delta:
                        if not self._in_thinking:
                            self._start_thinking()
                        self._thinking_buffer += delta

                elif event_type == "assistant.message":
                    # Final complete message — may contain usage
                    content = getattr(event, "content", "") or ""
                    if content:
                        self._end_thinking_if_active()
                        self._response_buffer = content
                        self._queue.put(PipelineOutput(
                            answer=self._response_buffer,
                            metadata={"event_type": "text"},
                            final=False,
                        ))
                    usage = getattr(event, "usage", None)
                    if usage:
                        self._capture_usage(usage)

                elif event_type == "assistant.reasoning":
                    # Final complete reasoning
                    content = getattr(event, "content", "") or ""
                    if content:
                        self._thinking_buffer = content
                    self._end_thinking_if_active()

                elif event_type == "session.idle":
                    # Terminal event — session finished
                    usage = getattr(event, "usage", None)
                    if usage:
                        self._capture_usage(usage)
                    break

                elif event_type in (
                    "session.compaction_start",
                    "session.compaction_complete",
                ):
                    logger.debug("Context compaction event: %s", event_type)

        except Exception:
            logger.exception("Error consuming Copilot SDK session events")
            raise
        finally:
            self._end_thinking_if_active()
            self._queue.put(_SENTINEL)

    # ── Sync generator (consumed by Flask thread) ─────────────────────

    def iter_outputs(self) -> Iterator[PipelineOutput]:
        """Yield PipelineOutput objects until the session stream ends.

        On GeneratorExit (stream cancelled), disconnects the SDK session.
        """
        try:
            while True:
                item = self._queue.get()
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
        """Normalize SDK usage object to the frontend-expected dict."""
        if isinstance(usage, dict):
            raw = usage
        else:
            raw = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None)
                    or getattr(usage, "promptTokens", None)
                    or 0,
                "completion_tokens": getattr(usage, "completion_tokens", None)
                    or getattr(usage, "completionTokens", None)
                    or 0,
                "total_tokens": getattr(usage, "total_tokens", None)
                    or getattr(usage, "totalTokens", None)
                    or 0,
            }

        self._usage = {
            "prompt_tokens": raw.get("prompt_tokens", 0),
            "completion_tokens": raw.get("completion_tokens", 0),
            "total_tokens": raw.get("total_tokens", 0),
            "context_window": raw.get("context_window"),
        }
