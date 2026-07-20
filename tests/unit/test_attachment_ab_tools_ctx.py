"""A/B arms must mount the same conversation-scoped attachment tools as the
normal chat path — otherwise a >inline_char_limit attachment routed to
"MANIFEST ONLY" is unreadable in both arms (the list/read/search tools never
mount because attachment_tools_ctx was omitted).

Importing the chat app pulls optional heavy deps that are absent locally (they
live in the Docker images), so stub the missing modules in sys.modules before
importing — the same technique the other attachment unit tests use.
"""
import sys
import types
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


def test_ab_arms_pass_attachment_tools_ctx_to_both_arms(monkeypatch):
    chat = ChatWrapper.__new__(ChatWrapper)   # no __init__: unit-scope only

    # Sentinel ctx returned by _attachment_tools_ctx; assert it reaches BOTH arms
    # and that it is computed exactly once (not per-arm — it hits Postgres).
    sentinel_ctx = object()
    ctx_calls = []

    def fake_ctx(conversation_id):
        ctx_calls.append(conversation_id)
        return sentinel_ctx

    chat._attachment_tools_ctx = fake_ctx

    variant_a = types.SimpleNamespace(name="A", provider="p", model="m")
    variant_b = types.SimpleNamespace(name="B", provider="p", model="m")
    chat.ab_pool = types.SimpleNamespace(
        sample_matchup=lambda: (variant_a, variant_b, True),
        variant_label_mode="post_vote_reveal",
    )
    chat._resolve_config_name = lambda cn: cn
    chat.update_config = lambda **kw: None
    chat._init_timestamps = lambda: {}
    context = types.SimpleNamespace(history=[{"role": "user"}], conversation_id=7)
    chat._prepare_chat_context = lambda *a, **k: (context, None)
    chat._resolve_runtime_ab_variant = lambda v: (v, None)

    # Each arm's pipeline.stream records its kwargs, then raises so both arms
    # error and the generator returns before any DB persistence runs.
    stream_a = MagicMock(side_effect=RuntimeError("stop-a"))
    stream_b = MagicMock(side_effect=RuntimeError("stop-b"))
    archi_a = types.SimpleNamespace(pipeline=types.SimpleNamespace(stream=stream_a))
    archi_b = types.SimpleNamespace(pipeline=types.SimpleNamespace(stream=stream_b))
    _created = iter([archi_a, archi_b])
    chat._create_variant_archi = lambda variant, **kw: next(_created)

    chat.archi = types.SimpleNamespace(
        vs_connector=types.SimpleNamespace(get_vectorstore=lambda: object())
    )
    # Formatter is constructed before stream() is called; neutralize it.
    monkeypatch.setattr(app_module, "PipelineEventFormatter", lambda **kw: MagicMock())

    events = list(chat.stream_ab_comparison(
        message=["hi"], conversation_id=7, client_id="c", is_refresh=False,
        server_received_msg_ts=None, client_sent_msg_ts=0.0, client_timeout=0,
        config_name="default", user_id=None,
    ))

    assert stream_a.call_args.kwargs.get("attachment_tools_ctx") is sentinel_ctx
    assert stream_b.call_args.kwargs.get("attachment_tools_ctx") is sentinel_ctx
    assert ctx_calls == [7]                       # computed once, shared by both arms
    assert any(e.get("type") == "error" for e in events)   # both arms raised
