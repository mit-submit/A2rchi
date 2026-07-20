"""Tests for ChatWrapper hybrid attachment routing and tool-context wiring."""
from src.interfaces.chat_app.app import ChatWrapper


class _Svc:
    def __init__(self, items):
        self._items = items

    def get_context_items(self, cid):
        return self._items

    def count_for_conversation(self, cid):
        return len(self._items)


def _wrapper(items, *, agent_capable=True, cfg=None):
    w = ChatWrapper.__new__(ChatWrapper)
    w.attachments_enabled = True
    w.attachments_config = {"inline_char_limit": 10, "text_budget_chars": 25,
                            "agent_tools_enabled": True, **(cfg or {})}
    w.attachment_service = _Svc(items)

    class _P:                        # pipeline stand-in
        pass

    class _A:
        pipeline = _P()

    if agent_capable:
        _P.refresh_agent = lambda self, **k: None
        # Agent-capable here means an agent that actually MOUNTS the attachment
        # tools (comops-like), which is what the routing gate must key on.
        _P.supports_attachment_tools = True
    w.archi = _A()
    return w


def _item(name, text):
    return {"filename": name, "kind": "document", "extracted_text": text, "extraction_meta": {}}


def test_small_items_stay_inline():
    w = _wrapper([_item("a.txt", "tiny")])
    routed = w._route_attachment_items(w.attachment_service.get_context_items(1))
    assert routed[0]["inline"] is True


def test_big_item_goes_manifest_when_agent_capable():
    w = _wrapper([_item("big.txt", "X" * 50)])
    routed = w._route_attachment_items(w.attachment_service.get_context_items(1))
    assert routed[0]["inline"] is False


def test_overflow_flips_oldest_not_truncates():
    w = _wrapper([_item("old.txt", "A" * 9), _item("mid.txt", "B" * 9), _item("new.txt", "C" * 9)])
    routed = w._route_attachment_items(w.attachment_service.get_context_items(1))
    assert [r["inline"] for r in routed] == [False, True, True]   # 27 > budget 25 → oldest flips


def test_everything_inline_when_not_agent_capable():
    w = _wrapper([_item("big.txt", "X" * 50)], agent_capable=False)
    routed = w._route_attachment_items(w.attachment_service.get_context_items(1))
    assert all(r["inline"] for r in routed)                        # D6 invariant


def test_kill_switch_forces_rung1():
    w = _wrapper([_item("big.txt", "X" * 50)], cfg={"agent_tools_enabled": False})
    assert w._attachment_tools_ctx(1) is None
    routed = w._route_attachment_items(w.attachment_service.get_context_items(1))
    assert all(r["inline"] for r in routed)


def test_ctx_built_when_capable_and_attachments_exist():
    w = _wrapper([_item("a.txt", "hi")])
    ctx = w._attachment_tools_ctx(1)
    assert ctx is not None and ctx.conversation_id == 1


def test_no_attachments_no_ctx():
    w = _wrapper([])
    assert w._attachment_tools_ctx(1) is None


def test_refresh_agent_without_declared_tool_support_stays_inline():
    # The bug: gating manifest routing on refresh_agent alone. refresh_agent is
    # defined on BaseReActAgent, so every ReAct pipeline has it, but only agents
    # that actually MOUNT the attachment tools may route to manifest mode. A
    # pipeline that exposes refresh_agent yet does not declare tool support must
    # keep large attachments INLINE, not promise tools that were never mounted.
    w = ChatWrapper.__new__(ChatWrapper)
    w.attachments_enabled = True
    w.attachments_config = {"inline_char_limit": 10, "text_budget_chars": 25,
                            "agent_tools_enabled": True}
    w.attachment_service = _Svc([_item("big.txt", "X" * 50)])

    class _P:
        def refresh_agent(self, **k):        # present, but no supports_attachment_tools
            return None

    class _A:
        pipeline = _P()

    w.archi = _A()
    assert w._attachment_tools_available() is False
    assert w._attachment_tools_ctx(1) is None
    routed = w._route_attachment_items(w.attachment_service.get_context_items(1))
    assert all(r["inline"] for r in routed)
