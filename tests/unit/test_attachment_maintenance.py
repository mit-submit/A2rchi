"""Startup sweep of conversations abandoned right after attach-to-new-chat.

Covers the ChatWrapper glue (config TTL -> service, disable sentinel, best-effort
error swallowing). The SQL itself is exercised in test_attachment_service.py.

Importing the chat app pulls optional heavy deps absent locally (they live in
the Docker images), so stub the missing modules in sys.modules before importing.
"""
import sys
from unittest.mock import MagicMock

_STUB_NAMES = (
    "langchain_community",
    "langchain_community.document_loaders",
    "langchain_community.document_loaders.text",
)
_before = set(sys.modules)
for _n in _STUB_NAMES:
    if _n not in sys.modules:
        sys.modules[_n] = MagicMock()
try:
    import src.interfaces.chat_app.app as app_module
    ChatWrapper = app_module.ChatWrapper
finally:
    # Purge everything this stubbed import added — the stubs AND the real
    # modules that imported against them — so later test files keep their
    # honest baseline import behavior instead of a MagicMock-poisoned cache.
    # Names bound above (app_module, ChatWrapper) survive the purge.
    for _n in set(sys.modules) - _before:
        sys.modules.pop(_n, None)


def _wrapper(ttl):
    chat = ChatWrapper.__new__(ChatWrapper)      # no __init__: unit-scope only
    chat.attachments_config = {} if ttl is None else {"abandoned_conversation_ttl_hours": ttl}
    chat.attachment_service = MagicMock()
    return chat


def test_wrapper_delegates_with_configured_ttl():
    chat = _wrapper(48)
    chat.attachment_service.sweep_abandoned_conversations.return_value = 3
    chat.sweep_abandoned_attachment_conversations()
    chat.attachment_service.sweep_abandoned_conversations.assert_called_once_with(48)


def test_wrapper_defaults_ttl_to_72_when_absent():
    chat = _wrapper(None)
    chat.attachment_service.sweep_abandoned_conversations.return_value = 0
    chat.sweep_abandoned_attachment_conversations()
    chat.attachment_service.sweep_abandoned_conversations.assert_called_once_with(72)


def test_wrapper_disabled_skips_service():
    chat = _wrapper(0)
    chat.sweep_abandoned_attachment_conversations()
    chat.attachment_service.sweep_abandoned_conversations.assert_not_called()


def test_wrapper_swallows_service_errors():
    chat = _wrapper(12)
    chat.attachment_service.sweep_abandoned_conversations.side_effect = RuntimeError("db down")
    # Startup sweep is best-effort: a failure must not propagate.
    chat.sweep_abandoned_attachment_conversations()
