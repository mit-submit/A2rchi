"""Unit tests for `normalize_token_usage`.

Covers every legacy token-usage shape the harness currently sees:
OpenAI-style, LangChain usage_metadata, LangChain response_metadata,
Ollama raw, nested under `usage` / `token_usage`, and message-like
objects exposing `.usage_metadata` or `.response_metadata`.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.harness.results_schema import TokenUsage
from src.harness.token_normalizer import normalize_token_usage, sum_token_usage


def test_none_returns_zeros():
    out = normalize_token_usage(None)
    assert out == TokenUsage()


def test_passthrough_token_usage():
    u = TokenUsage(prompt_tokens=7, completion_tokens=3, total_tokens=10)
    assert normalize_token_usage(u) is u


def test_openai_style_flat_dict():
    out = normalize_token_usage({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    assert out == TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)


def test_openai_style_missing_total_computes_sum():
    out = normalize_token_usage({"prompt_tokens": 10, "completion_tokens": 5})
    assert out.total_tokens == 15


def test_langchain_usage_metadata_style():
    out = normalize_token_usage({"input_tokens": 20, "output_tokens": 8, "total_tokens": 28})
    assert out == TokenUsage(prompt_tokens=20, completion_tokens=8, total_tokens=28)


def test_ollama_raw_style():
    out = normalize_token_usage({"prompt_eval_count": 100, "eval_count": 40})
    assert out == TokenUsage(prompt_tokens=100, completion_tokens=40, total_tokens=140)


def test_nested_under_usage_key():
    out = normalize_token_usage(
        {"usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}}
    )
    assert out.total_tokens == 33


def test_nested_under_token_usage_key():
    out = normalize_token_usage(
        {"token_usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}}
    )
    assert out == TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3)


def test_message_like_with_usage_metadata_attr():
    msg = SimpleNamespace(
        usage_metadata={"input_tokens": 5, "output_tokens": 5, "total_tokens": 10}
    )
    assert normalize_token_usage(msg).total_tokens == 10


def test_message_like_with_response_metadata_usage():
    msg = SimpleNamespace(
        response_metadata={"usage": {"prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75}}
    )
    assert normalize_token_usage(msg).total_tokens == 75


def test_message_like_with_response_metadata_ollama_raw():
    msg = SimpleNamespace(
        response_metadata={"prompt_eval_count": 42, "eval_count": 8}
    )
    assert normalize_token_usage(msg) == TokenUsage(
        prompt_tokens=42, completion_tokens=8, total_tokens=50
    )


def test_unknown_shape_returns_zeros_without_raising():
    out = normalize_token_usage({"something_else": 999})
    assert out == TokenUsage()


def test_sum_token_usage_mixed_shapes():
    inputs = [
        {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        {"prompt_eval_count": 100, "eval_count": 50},
        None,
        SimpleNamespace(usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
    ]
    total = sum_token_usage(inputs)
    assert total.prompt_tokens == 131
    assert total.completion_tokens == 66
    assert total.total_tokens == 197
