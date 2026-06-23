"""Context condensation middleware for LangGraph ReAct agents.

Implements the industry-standard ``before_model`` hook pattern to condense
tool-result messages *before every LLM call* inside the agent loop, preventing
context-window overflow.

Strategy (based on LangChain context-engineering best practices):
1. Count tokens for the current message list.
2. If under 80% of the model's context window, pass through unchanged.
3. If over 80%, condense large tool-result messages into summaries while
   preserving AI messages with ``tool_calls`` and their paired
   ``ToolMessage`` responses (breaking this pairing causes LLM errors).
4. As a final safeguard, trim oldest non-essential messages.

References:
- https://blog.langchain.com/context-engineering-for-agents/
- LangGraph ``AgentMiddleware.before_model`` API
- LangMem ``SummarizationNode`` pattern
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Trigger condensation when token usage exceeds this fraction of the window.
CONDENSATION_THRESHOLD = 0.80

# Maximum character length for a single tool-result message after condensation.
MAX_TOOL_RESULT_CHARS = 4_000

# Maximum character length for any single message content in the final output.
MAX_MESSAGE_CHARS = 8_000

# Number of recent messages always kept intact (to preserve active reasoning).
KEEP_RECENT_N = 6


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_tool_call_ids(message: BaseMessage) -> set:
    """Extract tool_call IDs from an AIMessage that requested tool calls."""
    tool_calls = getattr(message, "tool_calls", None) or []
    return {tc.get("id") or tc.get("tool_call_id", "") for tc in tool_calls if isinstance(tc, dict)}


def _is_tool_result(message: BaseMessage) -> bool:
    """Check if a message is a ToolMessage (tool result)."""
    return isinstance(message, ToolMessage)


def _content_length(message: BaseMessage) -> int:
    """Approximate character length of message content."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(part)) for part in content)
    return len(str(content))


def _truncate_content(content: str, max_chars: int) -> str:
    """Truncate content with an informative suffix."""
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n... [condensed: content truncated to fit context window]"


# ---------------------------------------------------------------------------
# Core condensation logic
# ---------------------------------------------------------------------------

def condense_messages(
    messages: List[BaseMessage],
    *,
    max_prompt_tokens: int,
    token_counter,
    llm=None,
) -> List[BaseMessage]:
    """Condense messages to fit within the token budget.

    This implements a multi-phase strategy:

    Phase 1 - Truncate large tool results
        Tool-result messages over ``MAX_TOOL_RESULT_CHARS`` are truncated.
        This is cheap and often sufficient.

    Phase 2 - Summarize old tool results via LLM
        If still over budget and an LLM is available, older tool-result
        messages are replaced with concise LLM-generated summaries.

    Phase 3 - Drop oldest messages
        As a last resort, the oldest non-system messages are dropped,
        being careful to never orphan a ToolMessage from its paired
        AIMessage (which would cause an LLM API error).

    Args:
        messages: The current message list from the agent state.
        max_prompt_tokens: Maximum allowed tokens for the prompt.
        token_counter: Callable that counts tokens for a message list.
        llm: Optional LLM instance for summarization (Phase 2).

    Returns:
        A condensed list of messages that fits within the budget.
    """
    if not messages:
        return messages

    token_count = token_counter(messages)
    if token_count < max_prompt_tokens:
        return messages

    logger.info(
        "Context condensation triggered: %d tokens exceeds budget %d",
        token_count, max_prompt_tokens,
    )

    # --- Phase 1: Truncate large tool results ---
    messages = _truncate_tool_results(messages)
    token_count = token_counter(messages)
    if token_count < max_prompt_tokens:
        logger.debug("Phase 1 (truncate tool results) sufficient: %d tokens", token_count)
        return messages

    # --- Phase 2: Summarize old tool results via LLM ---
    if llm is not None:
        messages = _summarize_old_tool_results(messages, llm=llm)
        token_count = token_counter(messages)
        if token_count < max_prompt_tokens:
            logger.debug("Phase 2 (summarize tool results) sufficient: %d tokens", token_count)
            return messages

    # --- Phase 3: Drop oldest non-essential messages ---
    messages = _drop_oldest_messages(messages, max_prompt_tokens, token_counter)
    token_count = token_counter(messages)
    logger.debug("Phase 3 (drop oldest) final token count: %d", token_count)

    return messages


def _truncate_tool_results(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Phase 1: Truncate oversized tool-result messages."""
    result = []
    for msg in messages:
        if _is_tool_result(msg) and _content_length(msg) > MAX_TOOL_RESULT_CHARS:
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            truncated = _truncate_content(content, MAX_TOOL_RESULT_CHARS)
            new_msg = ToolMessage(
                content=truncated,
                tool_call_id=getattr(msg, "tool_call_id", ""),
                name=getattr(msg, "name", None),
            )
            result.append(new_msg)
        elif _content_length(msg) > MAX_MESSAGE_CHARS and not isinstance(msg, SystemMessage):
            content = msg.content if isinstance(msg.content, str) else str(msg.content)
            truncated = _truncate_content(content, MAX_MESSAGE_CHARS)
            new_msg = msg.copy(update={"content": truncated})
            result.append(new_msg)
        else:
            result.append(msg)
    return result


def _summarize_old_tool_results(
    messages: List[BaseMessage],
    *,
    llm,
) -> List[BaseMessage]:
    """Phase 2: Use LLM to summarize older tool-result messages.

    Preserves the last KEEP_RECENT_N messages and only summarizes
    tool results in the older portion.
    """
    if len(messages) <= KEEP_RECENT_N:
        return messages

    recent = messages[-KEEP_RECENT_N:]
    older = messages[:-KEEP_RECENT_N]

    # Build set of tool_call_ids that have paired ToolMessages in recent
    recent_tool_call_ids = set()
    for msg in recent:
        if _is_tool_result(msg):
            recent_tool_call_ids.add(getattr(msg, "tool_call_id", ""))

    result = []
    for msg in older:
        if not _is_tool_result(msg) or _content_length(msg) <= MAX_TOOL_RESULT_CHARS:
            result.append(msg)
            continue

        # Summarize this tool result
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        tool_name = getattr(msg, "name", "tool") or "tool"
        try:
            summary_response = llm.invoke([
                SystemMessage(
                    content=(
                        "You are a context compression assistant. Summarize the following "
                        "tool output concisely, preserving key facts, numbers, and findings. "
                        "Keep the summary under 500 characters."
                    )
                ),
                HumanMessage(content=f"Tool '{tool_name}' returned:\n{content[:6000]}"),
            ])
            summary_text = (
                summary_response.content
                if isinstance(summary_response, BaseMessage)
                else str(summary_response)
            )
            summary_text = f"[Condensed summary of {tool_name} output]\n{summary_text}"
        except Exception as exc:
            logger.warning("LLM summarization failed for tool result: %s", exc)
            summary_text = _truncate_content(content, MAX_TOOL_RESULT_CHARS)

        new_msg = ToolMessage(
            content=summary_text,
            tool_call_id=getattr(msg, "tool_call_id", ""),
            name=getattr(msg, "name", None),
        )
        result.append(new_msg)

    return result + recent


def _drop_oldest_messages(
    messages: List[BaseMessage],
    max_prompt_tokens: int,
    token_counter,
) -> List[BaseMessage]:
    """Phase 3: Drop oldest messages while preserving tool-call pairing.

    Never drops:
    - SystemMessage (contains agent instructions)
    - An AIMessage whose tool_calls have a paired ToolMessage still present
    - A ToolMessage whose paired AIMessage is still present

    This prevents the LLM API error that occurs when tool_call_ids
    are orphaned.
    """
    if len(messages) <= 2:
        return messages

    # Separate system messages
    system_msgs = [m for m in messages if isinstance(m, SystemMessage)]
    non_system = [m for m in messages if not isinstance(m, SystemMessage)]

    if not non_system:
        return messages

    # Keep at least the last KEEP_RECENT_N non-system messages
    keep_count = min(KEEP_RECENT_N, len(non_system))
    recent = non_system[-keep_count:]
    droppable = non_system[:-keep_count] if keep_count < len(non_system) else []

    # Build set of tool_call_ids referenced in recent messages
    recent_tool_call_ids = set()
    for msg in recent:
        if _is_tool_result(msg):
            recent_tool_call_ids.add(getattr(msg, "tool_call_id", ""))
        for tc_id in _get_tool_call_ids(msg):
            recent_tool_call_ids.add(tc_id)

    # Try dropping from oldest first
    result = list(system_msgs)
    for msg in droppable:
        candidate = result + [msg] + recent
        token_count = token_counter(candidate)
        if token_count < max_prompt_tokens:
            # Still have room, keep this message
            result.append(msg)
        else:
            # Check if dropping this message would orphan a tool call
            msg_tool_ids = _get_tool_call_ids(msg)
            if _is_tool_result(msg):
                msg_tool_ids = {getattr(msg, "tool_call_id", "")}

            if msg_tool_ids & recent_tool_call_ids:
                # Can't drop — would orphan a paired message
                result.append(msg)
            else:
                logger.debug("Dropping message to fit context: %s", type(msg).__name__)

    result.extend(recent)
    return result


# ---------------------------------------------------------------------------
# MCP / external tool output wrapper
# ---------------------------------------------------------------------------

MAX_EXTERNAL_TOOL_OUTPUT_CHARS = 50_000


def truncate_tool_output(result: str, *, tool_name: str = "tool", max_chars: int = MAX_EXTERNAL_TOOL_OUTPUT_CHARS) -> str:
    """Truncate a tool output string if it exceeds the character limit.

    This is a simple function meant to be called from within tool wrappers
    (e.g. the ``make_synchronous`` wrapper in ``_build_mcp_tools``).  It does
    NOT mutate the tool object itself, avoiding the ``RecursionError`` that
    occurs when setting ``tool.func`` on a langchain ``BaseTool``.

    Args:
        result: The tool output string.
        tool_name: Name of the tool (for logging).
        max_chars: Maximum output characters before truncation.

    Returns:
        The original or truncated string.
    """
    if isinstance(result, str) and len(result) > max_chars:
        logger.warning(
            "Tool '%s' output truncated: %d -> %d chars",
            tool_name, len(result), max_chars,
        )
        return result[:max_chars] + "\n\n... [OUTPUT TRUNCATED - tool output exceeded size limit]"
    return result
