"""LangGraph AgentMiddleware for context-window management.

Implements the ``before_model`` hook to condense the message list
before every LLM call inside the ReAct agent loop.  This is the
industry-standard approach recommended by LangChain's context-engineering
guide and the LangGraph ``pre_model_hook`` / ``AgentMiddleware`` API.

Key design decisions:
- Uses ``before_model`` so condensation runs *between* every tool call,
  not just at the start of the agent loop.
- Returns condensed messages so the LLM sees a trimmed context while
  the full history is preserved in the graph state for tracing.
- Preserves AIMessage ↔ ToolMessage pairing to avoid provider API errors.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from langchain.agents.factory import AgentMiddleware

from src.archi.pipelines.agents.utils.context_condensation import (
    CONDENSATION_THRESHOLD,
    condense_messages,
)
from src.utils.logging import get_logger

logger = get_logger(__name__)


class ContextWindowMiddleware(AgentMiddleware):
    """Middleware that condenses context before each LLM call.

    Plugs into ``create_agent(middleware=[ContextWindowMiddleware(...)])``.
    """

    tools = ()  # No additional tools

    def __init__(
        self,
        *,
        llm=None,
        context_window: Optional[int] = None,
        condensation_threshold: float = CONDENSATION_THRESHOLD,
    ):
        """
        Args:
            llm: The LLM to use for summarisation (Phase 2).
                 If ``None``, only truncation and dropping are used.
            context_window: The model's context window in tokens.
                            If ``None``, condensation is skipped.
            condensation_threshold: Fraction of context window that
                                    triggers condensation (default 0.80).
        """
        self._llm = llm
        self._context_window = context_window
        self._threshold = condensation_threshold

    # ----- sync hook -----

    def before_model(self, state, runtime=None) -> Optional[Dict[str, Any]]:
        """Condense messages before each LLM call."""
        messages = state.get("messages") if isinstance(state, dict) else getattr(state, "messages", None)
        if not messages or self._context_window is None:
            return None

        llm = self._llm
        if llm is None:
            return None

        max_prompt_tokens = int(self._context_window * self._threshold)

        if not hasattr(llm, "get_num_tokens_from_messages"):
            return None

        try:
            token_count = llm.get_num_tokens_from_messages(messages)
        except Exception:
            return None

        if token_count < max_prompt_tokens:
            return None

        condensed = condense_messages(
            list(messages),
            max_prompt_tokens=max_prompt_tokens,
            token_counter=llm.get_num_tokens_from_messages,
            llm=llm,
        )

        logger.info(
            "ContextWindowMiddleware condensed %d -> %d messages (tokens: %d -> target <%d)",
            len(messages), len(condensed), token_count, max_prompt_tokens,
        )

        # Return under "messages" key so the state is updated and the
        # condensed version is used for the LLM call.
        return {"messages": condensed}

    # ----- async hook -----

    async def abefore_model(self, state, runtime=None) -> Optional[Dict[str, Any]]:
        """Async variant — delegates to sync implementation."""
        return self.before_model(state, runtime)
