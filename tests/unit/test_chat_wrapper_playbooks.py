"""Regression tests for the ChatWrapper <-> PlaybookService seam.

The Blueprint refactor (383d1e5b) moved the playbook side-table SQL onto
PlaybookService and left the chat flow calling ``self._playbook_svc()`` — but
the accessor lived only on FlaskAppWrapper. Every chat-flow call raised
AttributeError, silently swallowed by the surrounding best-effort excepts:
turn recording, the regenerate re-apply lookup and A/B turn recording all
no-oped (chips vanished on reload). No suite caught it because every layer
mocked or bypassed this exact seam.

These tests exercise the REAL accessor on a REAL ChatWrapper instance with
only the *service* mocked (never the accessor itself), so a missing or broken
accessor fails loudly instead of degrading silently.
"""
import pytest

flask = pytest.importorskip("flask", reason="chat app import chain needs flask")

from unittest.mock import MagicMock, patch

from src.interfaces.chat_app.app import ChatWrapper


def _wrapper() -> ChatWrapper:
    """A ChatWrapper without the heavy __init__ (config/DB); only what the
    tested methods touch is set."""
    wrapper = ChatWrapper.__new__(ChatWrapper)
    wrapper.pg_config = {"host": "unused-in-tests"}
    return wrapper


def _context(playbook_name="pb-name", playbook_id=7, conversation_id=55):
    ctx = MagicMock()
    ctx.playbook_name = playbook_name
    ctx.playbook_id = playbook_id
    ctx.conversation_id = conversation_id
    ctx.provider_used = "prov"
    ctx.model_used = "model"
    return ctx


def _factory_with(svc):
    factory = MagicMock()
    factory.playbook_service = svc
    return factory


def test_chat_wrapper_playbook_svc_uses_pooled_factory():
    svc = MagicMock()
    with patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        assert _wrapper()._playbook_svc() is svc


def test_insert_conversation_records_playbook_turn_through_real_accessor():
    """The user turn of a /name invocation must land in the side table via the
    real accessor — only the service is mocked, so a ChatWrapper without a
    working _playbook_svc fails here instead of warn-and-continuing."""
    wrapper = _wrapper()
    svc = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [(101,), (102,)]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch("src.interfaces.chat_app.app.psycopg2") as fake_pg, patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        fake_pg.connect.return_value = fake_conn
        message_ids = wrapper.insert_conversation(
            1,
            ("User", "run it", "2026-01-01T00:00:00Z"),
            ("archi", "done", "2026-01-01T00:00:30Z"),
            "",
            "",
            _context(),
        )

    assert message_ids == [101, 102]
    svc.record_playbook_turn.assert_called_once_with(101, "pb-name", 7)
    # Unified ledger: the same explicit /name use is also logged to playbook_invocations
    # (source=explicit, status=ok), keyed to the user turn's conversation + message id.
    svc.record_invocation.assert_called_once_with(
        55, 101, 7, "pb-name", source="explicit", status="ok")


def test_insert_conversation_plain_turn_does_not_touch_the_service():
    """No playbook on the turn -> no side-table write at all."""
    wrapper = _wrapper()
    svc = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [(201,), (202,)]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch("src.interfaces.chat_app.app.psycopg2") as fake_pg, patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        fake_pg.connect.return_value = fake_conn
        wrapper.insert_conversation(
            1,
            ("User", "plain question", "2026-01-01T00:00:00Z"),
            ("archi", "plain answer", "2026-01-01T00:00:30Z"),
            "",
            "",
            _context(playbook_name=None, playbook_id=None),
        )

    svc.record_playbook_turn.assert_not_called()
    svc.record_invocation.assert_not_called()  # a plain turn logs nothing


def test_insert_conversation_survives_a_failing_side_table_write():
    """The side-table write stays best-effort: a service error must not break
    the conversation insert itself (a failed migration degrades, not 500s)."""
    wrapper = _wrapper()
    svc = MagicMock()
    svc.record_playbook_turn.side_effect = RuntimeError("side table missing")
    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [(301,), (302,)]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor

    with patch("src.interfaces.chat_app.app.psycopg2") as fake_pg, patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        fake_pg.connect.return_value = fake_conn
        message_ids = wrapper.insert_conversation(
            1,
            ("User", "run it", "2026-01-01T00:00:00Z"),
            ("archi", "done", "2026-01-01T00:00:30Z"),
            "",
            "",
            _context(),
        )

    assert message_ids == [301, 302]
    svc.record_playbook_turn.assert_called_once()
    # The ledger write is independent best-effort: the side-table failure above must
    # not prevent it (nor vice-versa).
    svc.record_invocation.assert_called_once()


# ── M1: conversation loads degrade gracefully when the side table is missing ──

def test_convo_history_query_falls_back_when_side_table_missing():
    """A failed migration (no conversation_playbook_turns) must not 500 every
    conversation load: the helper rolls back the aborted transaction and
    retries with the no-playbooks variant."""
    import psycopg2

    from src.interfaces.chat_app import app as chat_app

    cursor = MagicMock()
    cursor.execute.side_effect = [psycopg2.errors.UndefinedTable("no cpt"), None]
    cursor.fetchall.return_value = [("User", "hi", 1, None, 0, None, None)]

    rows = chat_app._query_convo_history_rows(cursor, 42)

    assert rows == [("User", "hi", 1, None, 0, None, None)]
    cursor.connection.rollback.assert_called_once()
    fallback_sql = cursor.execute.call_args_list[1].args[0]
    assert "conversation_playbook_turns" not in fallback_sql
    assert "NULL AS playbook_name" in fallback_sql


def test_convo_history_query_single_roundtrip_when_table_exists():
    from src.interfaces.chat_app import app as chat_app

    cursor = MagicMock()
    cursor.fetchall.return_value = []

    chat_app._query_convo_history_rows(cursor, 42)

    assert cursor.execute.call_count == 1
    cursor.connection.rollback.assert_not_called()


def test_load_conversation_closes_connection_on_error():
    """M1's second half: an unexpected error mid-load must not leak the
    connection — it is closed in a finally, not only on the success path."""
    from src.interfaces.chat_app.app import FlaskAppWrapper

    wrapper = FlaskAppWrapper.__new__(FlaskAppWrapper)
    wrapper.pg_config = {"host": "unused-in-tests"}
    wrapper.chat = MagicMock()

    fake_conn = MagicMock()
    fake_conn.closed = False
    fake_cursor = MagicMock()
    fake_cursor.execute.side_effect = RuntimeError("boom mid-query")
    fake_conn.cursor.return_value = fake_cursor

    app = flask.Flask(__name__)
    with app.test_request_context(
        json={"conversation_id": 1, "client_id": "c1"}
    ), patch("src.interfaces.chat_app.app.psycopg2") as fake_pg:
        fake_pg.connect.return_value = fake_conn
        _resp, code = wrapper.load_conversation()

    assert code == 500
    fake_conn.close.assert_called_once()


# ── Unified ledger: auto (model-invoked) Playbook loads ───────────────────────

def _playbook_output(artifact=None, result="THE BODY", requested="rucio-triage"):
    from langchain_core.messages import AIMessage, ToolMessage
    from src.archi.utils.output_dataclass import PipelineOutput
    ai = AIMessage(content="", tool_calls=[
        {"name": "Playbook", "args": {"playbook": requested}, "id": "call_pb", "type": "tool_call"}])
    tm = ToolMessage(content=result, tool_call_id="call_pb", artifact=artifact)
    return PipelineOutput(answer="done", messages=[ai, tm])


def test_insert_tool_calls_records_auto_playbook_invocation_from_artifact():
    """A model-invoked Playbook load lands one auto row; the resolved id + name come
    from the ToolMessage artifact and status is ok."""
    wrapper = _wrapper()
    svc = MagicMock()
    output = _playbook_output(
        artifact={"kind": "playbook", "playbook_name": "rucio-triage", "playbook_id": 17})
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = MagicMock()

    with patch("src.interfaces.chat_app.app.psycopg2") as fake_pg, patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        fake_pg.connect.return_value = fake_conn
        wrapper.insert_tool_calls_from_output(9, 101, output)

    svc.record_invocation.assert_called_once_with(
        9, 101, 17, "rucio-triage", source="auto", status="ok")


def test_insert_tool_calls_records_auto_playbook_not_found_via_string():
    """A failed auto load has no artifact; the result string is classified and the
    requested name is recorded with the resolved status."""
    wrapper = _wrapper()
    svc = MagicMock()
    output = _playbook_output(
        artifact=None, requested="ghost",
        result="No playbook named 'ghost' is in your list. Available now:\n- a: d")
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = MagicMock()

    with patch("src.interfaces.chat_app.app.psycopg2") as fake_pg, patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        fake_pg.connect.return_value = fake_conn
        wrapper.insert_tool_calls_from_output(9, 101, output)

    svc.record_invocation.assert_called_once_with(
        9, 101, None, "ghost", source="auto", status="not_found")


def test_insert_tool_calls_auto_ledger_failure_does_not_break_tool_write():
    """A ledger failure must not propagate out of tool-call persistence."""
    wrapper = _wrapper()
    svc = MagicMock()
    svc.record_invocation.side_effect = RuntimeError("ledger missing")
    output = _playbook_output(
        artifact={"kind": "playbook", "playbook_name": "rucio-triage", "playbook_id": 1})
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = MagicMock()

    with patch("src.interfaces.chat_app.app.psycopg2") as fake_pg, patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        fake_pg.connect.return_value = fake_conn
        wrapper.insert_tool_calls_from_output(9, 101, output)  # must not raise

    svc.record_invocation.assert_called_once()


def test_resolve_auto_playbook_invocation_prefers_artifact():
    from src.interfaces.chat_app.app import _resolve_auto_playbook_invocation
    tc = {"name": "Playbook", "args": {"playbook": "req"}, "result": "BODY",
          "artifact": {"kind": "playbook", "playbook_name": "resolved", "playbook_id": 8}}
    assert _resolve_auto_playbook_invocation(tc) == (8, "resolved", "ok")


def test_resolve_auto_playbook_invocation_falls_back_to_string_classifier():
    from src.interfaces.chat_app.app import _resolve_auto_playbook_invocation
    tc = {"name": "Playbook", "args": {"playbook": "ghost"},
          "result": "No playbook named 'ghost' is in your list."}
    assert _resolve_auto_playbook_invocation(tc) == (None, "ghost", "not_found")


# ── Unified ledger: failed explicit /name (staging) ───────────────────────────

def test_stage_playbook_records_not_found_invocation():
    """A /name for a playbook that no longer resolves logs one explicit/not_found row
    with NULL ids (no conversation exists yet), best-effort."""
    from src.interfaces.chat_app.app import FlaskAppWrapper
    from src.utils.playbook_service import PlaybookNotFoundError

    wrapper = FlaskAppWrapper.__new__(FlaskAppWrapper)
    wrapper.pg_config = {"host": "unused-in-tests"}
    svc = MagicMock()
    svc.resolve_invokable_playbook.side_effect = PlaybookNotFoundError("gone")

    with patch.object(wrapper, "_resolve_playbook_owner", return_value=("owner-1", None)), patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        wrapper._stage_playbook_for_request("client-1", "ghost-pb")

    svc.record_invocation.assert_called_once_with(
        None, None, None, "ghost-pb", source="explicit", status="not_found")


# ── Unified ledger: A/B in-arm auto loads ─────────────────────────────────────

def test_record_ab_playbook_loads_records_per_arm_auto_loads():
    """Only tool_call_id-bearing (in-arm auto) events are recorded, tagged with the
    arm and keyed to that arm's assistant message id."""
    wrapper = _wrapper()
    svc = MagicMock()
    events = [
        {"type": "playbook_applied", "name": "staged", "arm": "a"},              # /name -> skipped
        {"type": "playbook_applied", "name": "auto-x", "playbook_id": 5,
         "tool_call_id": "call_1", "arm": "a"},
    ]
    with patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        wrapper._record_ab_playbook_loads(9, 202, "a", events)

    svc.record_invocation.assert_called_once_with(
        9, 202, 5, "auto-x", source="auto", status="ok", arm="a")


def test_record_ab_playbook_loads_noop_without_message_id():
    wrapper = _wrapper()
    svc = MagicMock()
    events = [{"type": "playbook_applied", "name": "x", "tool_call_id": "c", "arm": "b"}]
    with patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        wrapper._record_ab_playbook_loads(9, None, "b", events)

    svc.record_invocation.assert_not_called()


# ── Unified ledger: stream-path auto loads (recovered from trace events) ───────

def test_record_stream_playbook_loads_records_auto_from_trace_events():
    """The stream finalizes on the LAST streamed output (final answer only), so its
    auto Playbook loads are recovered from the trace events; an un-recorded
    tool_call_id lands one auto row keyed to the archi message id, source=auto."""
    wrapper = _wrapper()
    svc = MagicMock()
    events = [
        {"type": "text", "content": "hi"},
        {"type": "playbook_applied", "name": "rucio-triage", "playbook_id": 2052,
         "tool_call_id": "call_pb"},
    ]
    with patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        wrapper._record_stream_playbook_loads(55, 909, events, set())

    svc.record_invocation.assert_called_once_with(
        55, 909, 2052, "rucio-triage", source="auto", status="ok")


def test_record_stream_playbook_loads_skips_already_recorded_ids():
    """A load whose tool_call_id was already persisted by insert_tool_calls_from_output
    (a pipeline whose final output carried full messages) is skipped — no double count."""
    wrapper = _wrapper()
    svc = MagicMock()
    events = [{"type": "playbook_applied", "name": "p", "playbook_id": 1,
               "tool_call_id": "call_dup"}]
    with patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        wrapper._record_stream_playbook_loads(9, 101, events, {"call_dup"})

    svc.record_invocation.assert_not_called()


def test_record_stream_playbook_loads_noop_without_message_id_or_events():
    """No archi message id, or no trace events -> nothing recorded."""
    wrapper = _wrapper()
    svc = MagicMock()
    events = [{"type": "playbook_applied", "name": "p", "tool_call_id": "c"}]
    with patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        wrapper._record_stream_playbook_loads(9, None, events, set())  # falsy mid
        wrapper._record_stream_playbook_loads(9, 101, [], set())       # no events

    svc.record_invocation.assert_not_called()


def test_record_stream_playbook_loads_swallows_service_error():
    """A ledger failure must not propagate out of the stream finalize."""
    wrapper = _wrapper()
    svc = MagicMock()
    svc.record_invocation.side_effect = RuntimeError("ledger missing")
    events = [{"type": "playbook_applied", "name": "p", "playbook_id": 1,
               "tool_call_id": "call_pb"}]
    with patch(
        "src.utils.postgres_service_factory.PostgresServiceFactory.get_instance",
        return_value=_factory_with(svc),
    ):
        wrapper._record_stream_playbook_loads(9, 101, events, set())  # must not raise

    svc.record_invocation.assert_called_once()


# ── stream_ab_comparison whole-body exception guard ───────────────────────────

def _ab_ready_wrapper():
    """A ChatWrapper wired just enough to reach the _prepare_chat_context call
    inside stream_ab_comparison; every seam up to that call is mocked so the only
    behavior under test is how the generator handles _prepare_chat_context."""
    wrapper = _wrapper()
    wrapper.ab_pool = MagicMock()
    wrapper.ab_pool.sample_matchup.return_value = (MagicMock(), MagicMock(), True)
    wrapper._resolve_config_name = MagicMock(return_value="cfg")
    wrapper.update_config = MagicMock()
    wrapper._init_timestamps = MagicMock(return_value={})
    return wrapper


def _drain_ab(wrapper):
    from datetime import datetime
    return list(wrapper.stream_ab_comparison(
        ["hi"], None, "c1", False, datetime.now(), 0.0, 60.0, "cfg"))


def test_stream_ab_comparison_guards_prepare_context_failure():
    """A raise from _prepare_chat_context (live repro: create_conversation FK
    violation for a session user absent from the users table) must surface as one
    error event — not propagate into werkzeug after the response headers are sent
    and truncate the stream to a silent, message-less empty body."""
    wrapper = _ab_ready_wrapper()
    wrapper._prepare_chat_context = MagicMock(side_effect=RuntimeError("FK violation"))

    events = _drain_ab(wrapper)  # must NOT raise

    assert len(events) == 1
    assert events[0]["type"] == "error"


def test_stream_ab_comparison_timeout_error_code_is_unchanged():
    """The existing (None, 408) path still yields the mapped timeout error event
    with its status: the new whole-body guard wraps but does not swallow it."""
    wrapper = _ab_ready_wrapper()
    wrapper._prepare_chat_context = MagicMock(return_value=(None, 408))

    events = _drain_ab(wrapper)

    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["status"] == 408
