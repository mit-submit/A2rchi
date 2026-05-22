"""Bare LLM pipeline — sends questions directly to the model with no retrieval or tools."""

from __future__ import annotations

from typing import Any, Dict

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from src.archi.pipelines.classic_pipelines.base import BasePipeline
from src.archi.pipelines.classic_pipelines.utils import history_utils
from src.archi.utils.output_dataclass import PipelineOutput
from src.utils.logging import get_logger

logger = get_logger(__name__)


class BareLLMPipeline(BasePipeline):
    """Baseline pipeline: send question directly to LLM, no retrieval or tools."""

    def __init__(self, config: Dict[str, Any], *args, **kwargs) -> None:
        super().__init__(config, *args, **kwargs)
        self.chat_model = self.llms.get("chat_model")

    def invoke(self, **kwargs) -> PipelineOutput:
        history = kwargs.get("history")
        full_history = history_utils.tuplize_history(history)

        messages = []
        for role, content in full_history:
            if role.lower() in ("user", "human"):
                messages.append(HumanMessage(content=content))
            else:
                messages.append(AIMessage(content=content))

        response = self.chat_model.invoke(messages)
        answer = _extract_text_content(response.content) if hasattr(response, "content") else str(response)

        model_used = getattr(self.chat_model, "model_name", None) or getattr(self.chat_model, "model", "unknown")

        # Extract token usage from LangChain response metadata
        usage = _extract_usage(response)
        # Capture reasoning/thinking content if the model emitted it (langchain_ollama puts it
        # in additional_kwargs["reasoning_content"] when ChatOllama is constructed with reasoning=True).
        thinking_content = _extract_thinking(response)

        return PipelineOutput(
            answer=answer,
            source_documents=[],
            messages=[],
            metadata={
                "question": full_history[-1][1] if full_history else "",
                "model_used": model_used,
                "pipeline_used": self.__class__.__name__,
                "usage": usage,
                "thinking_content": thinking_content,
            },
        )


def _extract_thinking(response) -> str:
    """Return the model's reasoning/thinking trace if present.

    langchain_ollama 1.0.x surfaces Ollama's `message.thinking` field as
    `AIMessage.additional_kwargs["reasoning_content"]` whenever the
    ChatOllama instance was constructed with `reasoning=True`. Other
    providers may use different keys; we check the common ones.
    """
    additional = getattr(response, "additional_kwargs", None) or {}
    for key in ("reasoning_content", "thinking", "reasoning"):
        v = additional.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def _extract_text_content(content) -> str:
    """Normalize provider content blocks into the answer text.

    OpenAI's Responses API can surface AIMessage.content as a list containing
    reasoning and text blocks. Benchmark outputs expect answer to be a string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(text, dict) and isinstance(text.get("value"), str):
                    parts.append(text["value"])
        return "\n\n".join(part for part in parts if part)
    return str(content)


def _extract_usage(response) -> dict:
    """Extract normalized token usage from a LangChain AIMessage response."""
    # Try usage_metadata (LangChain standard)
    usage_metadata = getattr(response, "usage_metadata", None)
    if isinstance(usage_metadata, dict):
        prompt = usage_metadata.get("input_tokens", 0)
        completion = usage_metadata.get("output_tokens", 0)
        if prompt or completion:
            return {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
            }

    # Try response_metadata (provider-specific)
    response_metadata = getattr(response, "response_metadata", None) or {}
    usage = response_metadata.get("usage") or response_metadata.get("token_usage")
    if usage:
        prompt = usage.get("prompt_tokens") or usage.get("input_tokens", 0)
        completion = usage.get("completion_tokens") or usage.get("output_tokens", 0)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
    # Ollama format
    if "prompt_eval_count" in response_metadata or "eval_count" in response_metadata:
        prompt = response_metadata.get("prompt_eval_count", 0)
        completion = response_metadata.get("eval_count", 0)
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }

    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
