"""App-level attachment-binding invariants for normal, refresh, and A/B turns.

bind_unbound_to_message is covered in isolation (test_attachment_service.py), but
nothing pins *where* app.py calls it. Two invariants matter:

  (a) it must target the USER message — message_ids[0] on a single-response turn
      (insert_conversation stores user-then-archi, so index 0 is the user turn)
      and user_prompt_mid on an A/B turn — so chips land on the user bubble;
      flipping to message_ids[-1] would silently bind them to the assistant turn.
  (b) it must be skipped on a refresh/regenerate (the `not context.is_refresh`
      guard on both call sites), so a regenerate reuses the conversation's
      existing attachments instead of re-running the UPDATE.

app.py can't be imported in the minimal test env (it transitively needs
langchain_community, absent here) and a ChatWrapper can't be constructed in unit
scope, so these are structural pins over the app.py source — the highest-fidelity
guard available for those exact drifts without a live stack.
"""
import re
from pathlib import Path

_APP_PY = Path(__file__).resolve().parents[2] / "src" / "interfaces" / "chat_app" / "app.py"


def _source():
    return _APP_PY.read_text()


def _normalized_source():
    return re.sub(r"\s+", " ", _source())


def _method_body(src, name):
    start = src.index(f"    def {name}(")
    rest = src[start + 1 :]
    return src[start : start + 1 + rest.index("\n    def ")]


def _paren_expr(text, after):
    open_idx = text.index("(", text.index(after))
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1 : i]
    raise AssertionError("unbalanced parens")


def test_insert_conversation_stores_user_before_archi_on_a_normal_turn():
    # The non-refresh branch lists the user row before the archi row, so the
    # returned message_ids[0] — the value the single-response bind targets — is
    # the user message.
    rhs = _paren_expr(_method_body(_source(), "insert_conversation"), "insert_tups =")
    non_refresh, sep, _ = rhs.partition("if not is_refresh")
    assert sep, "expected a `if not is_refresh` conditional in insert_tups"
    assert non_refresh.index("user_sender") < non_refresh.index("ARCHI_SENDER")


def test_insert_conversation_refresh_stores_archi_only():
    # A refresh/regenerate inserts only the archi row — no user row is created,
    # which is why the bind must be skipped rather than retargeted on refresh.
    rhs = _paren_expr(_method_body(_source(), "insert_conversation"), "insert_tups =")
    _, _, else_list = rhs.partition("else")
    assert "ARCHI_SENDER" in else_list
    assert "user_sender" not in else_list
    assert "user_content" not in else_list


def test_single_response_binds_the_user_message_and_skips_refresh():
    n = _normalized_source()
    assert (
        "if self.attachments_enabled and message_ids and not context.is_refresh:" in n
    )
    assert "bind_unbound_to_message( context.conversation_id, message_ids[0] )" in n


def test_ab_path_binds_the_user_message_and_skips_refresh():
    n = _normalized_source()
    assert (
        "if self.attachments_enabled and user_prompt_mid and not context.is_refresh:"
        in n
    )
    assert "bind_unbound_to_message( context.conversation_id, user_prompt_mid )" in n
    # user_prompt_mid is captured from the id of the user message the A/B path
    # stores first (a single-row insert, only when not a refresh), so the A/B
    # bind targets the user bubble just like the single-response path.
    assert "user_prompt_mid = inserted_ids[0] if inserted_ids else None" in n


def test_binding_is_only_invoked_at_the_two_guarded_call_sites():
    # A third, unguarded bind_unbound_to_message call would defeat the refresh
    # skip; pin the two known call sites so a new one is a deliberate change.
    assert _source().count("bind_unbound_to_message(") == 2
