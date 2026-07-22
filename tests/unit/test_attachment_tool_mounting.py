"""Per-run attachment tool mounting is comops-owned (opt-in by override).

BaseReActAgent is attachment-agnostic: its packing step mounts only vector
tools. An agent opts in by overriding _prepare_agent_inputs to rebuild the
toolbox once more with the conversation-scoped attachment tools merged in
(CMSCompOpsAgent does exactly this). These tests drive the real packing and
refresh path with a stubbed _create_agent and inspect the final built agent.

The comops-specific test imports cms_comp_ops_agent, whose import chain needs
psycopg2 (present in the Docker images) - it importorskips elsewhere.
"""

import pytest

from src.archi.pipelines.agents.base_react import BaseReActAgent
from src.archi.pipelines.agents.utils.run_context import RunContext
from src.utils.attachment_reader import AttachmentToolContext


class _Svc:
    def get_context_items(self, cid):
        return []

    def get_for_tools(self, cid, filename):
        return None


class _AgentStub:
    def __init__(self, tools, middleware):
        self.tools = list(tools)
        self.middleware = list(middleware)


class _OptInAgent(BaseReActAgent):
    """Minimal opt-in subclass mirroring CMSCompOpsAgent's _extra_run_tools."""

    supports_attachment_tools = True

    def _extra_run_tools(self, **kwargs):
        ctx = kwargs.get("attachment_tools_ctx")
        if not ctx:
            return []
        from src.archi.pipelines.agents.tools import (
            create_attachment_list_tool,
            create_attachment_read_tool,
            create_attachment_search_tool,
        )
        return [
            create_attachment_list_tool(ctx),
            create_attachment_read_tool(ctx),
            create_attachment_search_tool(ctx),
        ]


def _ctx():
    return AttachmentToolContext(conversation_id=1, service=_Svc(), caps={})


def _wire(agent):
    """Minimal run wiring for an agent built without __init__ (unit scope)."""
    agent._run_ctx = RunContext()
    agent.agent = None
    agent._active_tools = []
    agent._active_middleware = []
    agent._static_tools = []       # `tools` property returns [] without rebuild
    agent._static_middleware = []
    agent._mcp_tools = None
    agent.selected_tool_names = []
    agent.agent_llm = None         # lacks token counting -> trimming skipped
    agent._create_agent = lambda tools, middleware: _AgentStub(tools, middleware)
    return agent


def _tool_names(agent_stub):
    return [getattr(t, "name", "") for t in agent_stub.tools]


ATTACHMENT_TOOL_NAMES = [
    "list_attachment_files", "read_attachment_file", "search_attachment_files",
]


def test_base_prepare_mounts_no_attachment_tools():
    # The base class is attachment-agnostic: even with a ctx in the request,
    # plain BaseReActAgent packs no attachment tools.
    agent = _wire(BaseReActAgent.__new__(BaseReActAgent))
    agent._prepare_agent_inputs(history=[], vectorstore=None,
                                attachment_tools_ctx=_ctx())
    assert agent.agent is None or _tool_names(agent.agent) == []


def test_opt_in_agent_mounts_three_named_tools():
    agent = _wire(_OptInAgent.__new__(_OptInAgent))
    agent._prepare_agent_inputs(history=[], vectorstore=None,
                                attachment_tools_ctx=_ctx())
    assert _tool_names(agent.agent) == ATTACHMENT_TOOL_NAMES


def test_opt_in_agent_without_ctx_mounts_nothing():
    agent = _wire(_OptInAgent.__new__(_OptInAgent))
    agent._prepare_agent_inputs(history=[], vectorstore=None)
    assert agent.agent is None or _tool_names(agent.agent) == []


def test_merge_vector_and_attachment_tools():
    # The second rebuild must keep the vector tools AND add the attachment
    # tools - dropping either group is the regression this test pins.
    agent = _wire(_OptInAgent.__new__(_OptInAgent))
    vector_stub = object()
    agent._vector_tools = [vector_stub]
    # vectorstore must be truthy: the `elif vectorstore is None` branch in the
    # base packing nulls _vector_tools when the kwarg is absent.
    agent._prepare_agent_inputs(history=[], vectorstore=object(),
                                attachment_tools_ctx=_ctx())
    tools = agent.agent.tools
    assert tools[0] is vector_stub
    assert [getattr(t, "name", "") for t in tools[1:]] == ATTACHMENT_TOOL_NAMES


def test_comops_override_mounts_three_named_tools():
    # The real opt-in lives on CMSCompOpsAgent; its import chain needs
    # psycopg2, so this runs where that is installed (the Docker images).
    pytest.importorskip("psycopg2")
    from src.archi.pipelines.agents.cms_comp_ops_agent import CMSCompOpsAgent

    agent = _wire(CMSCompOpsAgent.__new__(CMSCompOpsAgent))
    agent._prepare_agent_inputs(history=[], vectorstore=None,
                                attachment_tools_ctx=_ctx())
    assert _tool_names(agent.agent) == ATTACHMENT_TOOL_NAMES
