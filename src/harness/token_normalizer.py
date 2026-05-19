"""Token usage normalization.

The benchmark harness sees token usage in many shapes depending on the
provider (OpenAI, Ollama raw, LangChain response_metadata,
usage_metadata, per-message vs per-response). This module collapses all
of them into a single canonical `TokenUsage` so downstream code never
has to branch on provider-specific keys.

Supported input shapes:

- `None` -> zeros
- `TokenUsage` -> returned unchanged
- pydantic BaseModel with the same fields -> converted
- dict with any of:
    * `prompt_tokens` / `completion_tokens` / `total_tokens` (OpenAI)
    * `input_tokens` / `output_tokens` / `total_tokens` (LangChain usage_metadata)
    * `prompt_eval_count` / `eval_count` (Ollama raw)
    * nested `usage` or `usage_metadata` sub-dict (any of the above)
- LangChain-style message-like object with `.usage_metadata` or `.response_metadata`
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from src.harness.results_schema import TokenUsage


def _from_mapping(data: Mapping[str, Any]) -> Optional[TokenUsage]:
    """Try to pull a TokenUsage out of a flat mapping. Returns None if no known keys.

    Key-name priority: OpenAI-style > LangChain usage_metadata-style > Ollama raw.
    Each branch requires at least one of its discriminating keys (prompt/completion
    pair) to be present, so a dict that only has `total_tokens` plus LangChain-style
    input/output keys is correctly classified as LangChain, not as a zero-filled
    OpenAI match.
    """
    # OpenAI-style (prompt_tokens / completion_tokens)
    p = data.get("prompt_tokens")
    c = data.get("completion_tokens")
    if p is not None or c is not None:
        t = data.get("total_tokens")
        p_i = int(p or 0)
        c_i = int(c or 0)
        t_i = int(t) if t is not None else p_i + c_i
        return TokenUsage(prompt_tokens=p_i, completion_tokens=c_i, total_tokens=t_i)

    # LangChain usage_metadata style (input_tokens / output_tokens)
    p = data.get("input_tokens")
    c = data.get("output_tokens")
    if p is not None or c is not None:
        t = data.get("total_tokens")
        p_i = int(p or 0)
        c_i = int(c or 0)
        t_i = int(t) if t is not None else p_i + c_i
        return TokenUsage(prompt_tokens=p_i, completion_tokens=c_i, total_tokens=t_i)

    # Ollama raw (prompt_eval_count / eval_count)
    p = data.get("prompt_eval_count")
    c = data.get("eval_count")
    if p is not None or c is not None:
        p_i = int(p or 0)
        c_i = int(c or 0)
        return TokenUsage(prompt_tokens=p_i, completion_tokens=c_i, total_tokens=p_i + c_i)

    return None


def normalize_token_usage(source: Any) -> TokenUsage:
    """Coerce any supported input shape into a canonical `TokenUsage`.

    Unknown shapes return zeros. This function never raises on shape
    mismatches because token-usage reporting is best-effort: a run
    should not fail just because the provider did not report tokens.
    """
    if source is None:
        return TokenUsage()

    if isinstance(source, TokenUsage):
        return source

    # Message-like: try .usage_metadata, then .response_metadata.usage
    usage_metadata = getattr(source, "usage_metadata", None)
    if isinstance(usage_metadata, Mapping):
        out = _from_mapping(usage_metadata)
        if out is not None:
            return out

    response_metadata = getattr(source, "response_metadata", None)
    if isinstance(response_metadata, Mapping):
        nested = response_metadata.get("usage") or response_metadata.get("token_usage")
        if isinstance(nested, Mapping):
            out = _from_mapping(nested)
            if out is not None:
                return out
        out = _from_mapping(response_metadata)
        if out is not None:
            return out

    if isinstance(source, Mapping):
        # Some callers pass {"usage": {...}} or {"token_usage": {...}}
        for key in ("token_usage", "usage", "usage_metadata"):
            nested = source.get(key)
            if isinstance(nested, Mapping):
                out = _from_mapping(nested)
                if out is not None:
                    return out
        out = _from_mapping(source)
        if out is not None:
            return out

    # Pydantic model with matching fields
    if hasattr(source, "model_dump"):
        try:
            out = _from_mapping(source.model_dump())
            if out is not None:
                return out
        except Exception:
            pass

    return TokenUsage()


def sum_token_usage(usages: Iterable[Any]) -> TokenUsage:
    """Sum an iterable of any-shaped token usage inputs."""
    total = TokenUsage()
    for u in usages:
        total = total + normalize_token_usage(u)
    return total
