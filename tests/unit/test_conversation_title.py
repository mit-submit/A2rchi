"""ChatWrapper conversation-title derivation — single source of truth.

The title formula was copy-pasted between create_conversation and the
attach-time _set_conversation_title; a shared helper keeps them in lockstep.
"""
from src.interfaces.chat_app.app import ChatWrapper


def test_short_content_unchanged():
    assert ChatWrapper._derive_conversation_title("hello") == "hello"


def test_exactly_20_has_no_ellipsis():
    assert ChatWrapper._derive_conversation_title("y" * 20) == "y" * 20


def test_over_20_truncates_with_ellipsis():
    assert ChatWrapper._derive_conversation_title("x" * 25) == "x" * 20 + "..."
