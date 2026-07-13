"""A/B and counter-honesty seams for the "playbook applied" activity step.

Covers three fixes that keep the applied-playbook step correct without a live
stream:
  1. the A/B stream emits the staged /name playbook once *per arm* (the A/B
     client drops any event lacking an ``arm`` tag), built by a pure helper;
  2. suppressing a playbook tool row must not inflate the persisted
     ``total_tool_calls`` counter; and
  3. an artifact-bearing ToolMessage with no ``tool_call_id`` must not poison
     the playbook-suppression set (which would swallow an unrelated tool_end).
"""
from langchain_core.messages import AIMessage, ToolMessage

from src.archi.utils.output_dataclass import PipelineOutput
from src.interfaces.chat_app.event_formatter import (
    PipelineEventFormatter,
    build_playbook_applied_events,
    playbook_loads_from_events,
)


def _formatter():
    return PipelineEventFormatter(message_content_fn=lambda m: getattr(m, "content", ""))


def _tool_start(tool_calls):
    return PipelineOutput(
        answer="", messages=[AIMessage(content="", tool_calls=tool_calls)],
        metadata={"event_type": "tool_start"},
    )


def _tool_output(msg):
    return PipelineOutput(answer="", messages=[msg], metadata={"event_type": "tool_output"})


def _playbook_msg(tc_id, name="p", body="BODY"):
    return ToolMessage(
        content=body, tool_call_id=tc_id,
        artifact={"kind": "playbook", "playbook_name": name},
    )


# ── FIX-1: per-arm playbook_applied events for the A/B stream ──────────────────────────

def test_build_ab_events_none_pending_is_empty():
    assert build_playbook_applied_events(None, ("a", "b")) == []


def test_build_ab_events_missing_name_is_empty():
    assert build_playbook_applied_events({"name": None, "body": "x"}, ("a", "b")) == []
    assert build_playbook_applied_events({"body": "x"}, ("a", "b")) == []


def test_build_ab_events_staged_emits_one_tagged_event_per_arm():
    evts = build_playbook_applied_events(
        {"name": "transfer-check", "body": "STEP 1"}, ("a", "b"))
    assert [e["arm"] for e in evts] == ["a", "b"]
    assert all(e["type"] == "playbook_applied" for e in evts)
    assert all(e["name"] == "transfer-check" for e in evts)
    assert all(e["body"] == "STEP 1" for e in evts)


def test_build_ab_events_body_defaults_to_empty_string():
    evts = build_playbook_applied_events({"name": "p"}, ("a", "b"))
    assert len(evts) == 2
    assert all(e["body"] == "" for e in evts)


# ── FIX-2: suppressed playbook rows must not inflate total_tool_calls ──────────────────

def test_auto_pickup_playbook_leaves_tool_count_zero():
    # tool_start counts the Playbook call, then the suppressed tool_output undoes it.
    fmt = _formatter()
    list(fmt.process(_tool_start(
        [{"name": "Playbook", "args": {"playbook": "p"}, "id": "call_pb", "type": "tool_call"}])))
    assert fmt.tool_call_count == 1
    list(fmt.process(_tool_output(_playbook_msg("call_pb"))))
    assert fmt.tool_call_count == 0


def test_deferred_playbook_output_leaves_tool_count_zero():
    # No preceding tool_start (deferred): the row was never counted, so no underflow.
    fmt = _formatter()
    list(fmt.process(_tool_output(_playbook_msg("call_pb"))))
    assert fmt.tool_call_count == 0


def test_ordinary_plus_playbook_counts_exactly_one():
    fmt = _formatter()
    list(fmt.process(_tool_start([
        {"name": "search", "args": {"q": "x"}, "id": "call_s", "type": "tool_call"},
        {"name": "Playbook", "args": {"playbook": "p"}, "id": "call_pb", "type": "tool_call"},
    ])))
    assert fmt.tool_call_count == 2
    list(fmt.process(_tool_output(_playbook_msg("call_pb"))))
    list(fmt.process(_tool_output(ToolMessage(content="rows", tool_call_id="call_s"))))
    assert fmt.tool_call_count == 1


def test_ordinary_only_tool_count_unchanged():
    fmt = _formatter()
    list(fmt.process(_tool_start(
        [{"name": "search", "args": {"q": "x"}, "id": "call_s", "type": "tool_call"}])))
    list(fmt.process(_tool_output(ToolMessage(content="rows", tool_call_id="call_s"))))
    assert fmt.tool_call_count == 1


# ── Unified ledger: collect in-arm auto Playbook loads from an arm's events ────────────

def test_playbook_loads_collects_only_in_arm_auto_events():
    """Auto loads inside an arm carry a tool_call_id; the up-front /name per-arm
    events (from build_playbook_applied_events) have none and are recorded as
    explicit elsewhere — so only the tool_call_id-bearing events are collected."""
    events = [
        {"type": "playbook_applied", "name": "staged", "arm": "a"},  # /name, no tool_call_id
        {"type": "text", "content": "hi", "arm": "a"},
        {"type": "playbook_applied", "name": "auto-one", "playbook_id": 5,
         "tool_call_id": "call_1", "arm": "a"},
        {"type": "playbook_applied", "name": "auto-two",
         "tool_call_id": "call_2", "arm": "a"},  # no id resolved -> None
    ]
    loads = playbook_loads_from_events(events)
    assert loads == [
        {"name": "auto-one", "playbook_id": 5, "tool_call_id": "call_1"},
        {"name": "auto-two", "playbook_id": None, "tool_call_id": "call_2"},
    ]


def test_playbook_loads_carries_tool_call_id_for_stream_dedupe():
    """Each load now also carries its tool_call_id so the stream path can dedupe a
    recovered load against ids insert_tool_calls_from_output already persisted. The
    A/B recorder reads only name/playbook_id via .get, so the extra key is harmless."""
    events = [
        {"type": "playbook_applied", "name": "auto", "playbook_id": 9,
         "tool_call_id": "call_z"},
    ]
    [load] = playbook_loads_from_events(events)
    assert load["tool_call_id"] == "call_z"
    # existing callers read via .get and ignore the extra key
    assert load.get("name") == "auto"
    assert load.get("playbook_id") == 9


def test_playbook_loads_empty_when_no_auto_events():
    assert playbook_loads_from_events([]) == []
    assert playbook_loads_from_events(None) == []
    assert playbook_loads_from_events(
        [{"type": "playbook_applied", "name": "staged", "arm": "b"}]) == []


# ── FIX-3: an id-less artifact message must not poison the suppression set ─────────────

def test_empty_tool_call_id_artifact_does_not_suppress_unrelated_tool_end():
    fmt = _formatter()
    # artifact present but no tool_call_id and no pending ids: must fall through to the
    # normal tool-row path, NOT add "" to _playbook_output_ids.
    list(fmt.process(_tool_output(_playbook_msg(""))))
    assert "" not in fmt._playbook_output_ids
    # a later id-less tool_end is a real tool_end, not swallowed.
    assert [e["type"] for e in fmt._on_tool_end(None, {"tool_call_id": ""})] == ["tool_end"]
