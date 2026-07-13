"""The Playbook loader is surfaced in the agent-activity trace as a distinct
"playbook applied" step (for both `/name` and auto-pickup), not a generic tool row.

These tests cover the two backend seams that make that work:
  1. the Playbook tool returns (content, artifact) so the resolved name/body ride
     on the ToolMessage without entering the model's context; and
  2. the event formatter turns that ToolMessage into a single `playbook_applied`
     event (carrying name + body) and suppresses the tool_start/output/end for it,
     while leaving ordinary tools untouched.
"""
from unittest.mock import MagicMock

from langchain_core.messages import ToolMessage

from src.utils.playbook_service import Playbook
from src.archi.pipelines.agents.tools.playbook_tools import (
    create_playbook_tool, set_playbook_owner,
)
from src.interfaces.chat_app.event_formatter import PipelineEventFormatter
from src.archi.utils.output_dataclass import PipelineOutput


def _formatter():
    return PipelineEventFormatter(message_content_fn=lambda m: getattr(m, "content", ""))


def _playbook_tool(name="transfer-check", body="THE BODY", owner="c1"):
    svc = MagicMock()
    svc.resolve_invokable_playbook.return_value = Playbook(
        id=1, name=name, description="d", body=body, owner_id=owner)
    set_playbook_owner(owner)
    return create_playbook_tool(svc, lambda: owner)


def _tool_message(tool, playbook_name):
    return tool.invoke({
        "type": "tool_call", "name": "Playbook",
        "args": {"playbook": playbook_name}, "id": "call_pb",
    })


# ── The tool: content_and_artifact keeps the name out of the model's context ──────────

def test_plain_invoke_returns_only_body():
    # The model-facing content is unchanged: still just the body string.
    tool = _playbook_tool(body="THE BODY")
    assert tool.invoke({"playbook": "transfer-check"}) == "THE BODY"


def test_toolcall_invoke_carries_name_and_body_as_artifact():
    tool = _playbook_tool(name="transfer-check", body="THE BODY")
    tm = _tool_message(tool, "transfer-check")
    assert isinstance(tm, ToolMessage)
    assert tm.content == "THE BODY"
    # The artifact also carries the resolved playbook_id (server-side only) so the
    # auto-load ledger can store the real id, not just the name.
    assert tm.artifact == {"kind": "playbook", "playbook_name": "transfer-check", "playbook_id": 1}


# ── The formatter: Playbook loader -> a single playbook_applied step ───────────────────

def test_playbook_output_becomes_playbook_applied_with_body():
    tool = _playbook_tool(name="transfer-check", body="STEP 1\nSTEP 2")
    tm = _tool_message(tool, "transfer-check")
    fmt = _formatter()
    out = PipelineOutput(answer="", messages=[tm], metadata={"event_type": "tool_output"})
    events = list(fmt._on_tool_output(out, {}))
    assert len(events) == 1
    evt = events[0]
    assert evt["type"] == "playbook_applied"
    assert evt["name"] == "transfer-check"
    assert evt["body"] == "STEP 1\nSTEP 2"
    # The resolved id rides on the event too (UI ignores unknown keys); it is the
    # requesting user's own playbook id, not a credential.
    assert evt["playbook_id"] == 1


def test_playbook_applied_event_omits_id_when_artifact_has_none():
    # A legacy/id-less artifact (name only) must not force a null playbook_id key.
    fmt = _formatter()
    tm = ToolMessage(content="BODY", tool_call_id="call_pb",
                     artifact={"kind": "playbook", "playbook_name": "p"})
    out = PipelineOutput(answer="", messages=[tm], metadata={"event_type": "tool_output"})
    evt = list(fmt._on_tool_output(out, {}))[0]
    assert evt["type"] == "playbook_applied"
    assert "playbook_id" not in evt


def test_playbook_tool_end_is_suppressed():
    tool = _playbook_tool()
    tm = _tool_message(tool, "transfer-check")
    fmt = _formatter()
    out = PipelineOutput(answer="", messages=[tm], metadata={"event_type": "tool_output"})
    list(fmt._on_tool_output(out, {}))  # marks call_pb as a playbook step
    assert list(fmt._on_tool_end(out, {"tool_call_id": "call_pb"})) == []


def test_ordinary_tool_still_emits_start_output_and_end():
    fmt = _formatter()
    msg = ToolMessage(content="rows...", tool_call_id="call_x")
    out = PipelineOutput(answer="", messages=[msg], metadata={"event_type": "tool_output"})
    types = [e["type"] for e in fmt._on_tool_output(out, {})]
    assert types == ["tool_start", "tool_output"]
    assert [e["type"] for e in fmt._on_tool_end(out, {"tool_call_id": "call_x"})] == ["tool_end"]
