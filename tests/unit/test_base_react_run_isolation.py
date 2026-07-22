"""One BaseReActAgent instance serves every request thread (Flask threaded=True),
so a concurrent request must not swap the run's agent/memory mid-stream.

These tests drive the real _begin_run / _prepare_agent_inputs / _extra_run_tools
path with a stubbed _create_agent (so no LLM/langgraph is built).
"""

import threading

from langchain_core.documents import Document

from src.archi.pipelines.agents.base_react import BaseReActAgent
from src.archi.pipelines.agents.utils.run_context import RunContext
from src.utils.attachment_reader import AttachmentToolContext


class _Svc:
    def get_context_items(self, cid):
        return []

    def get_for_tools(self, cid, filename):
        return None


class _AgentStub:
    """Distinguishable stand-in for a compiled agent; remembers its toolset."""

    def __init__(self, tools, middleware):
        self.tools = list(tools)
        self.middleware = list(middleware)


def _ctx(cid):
    return AttachmentToolContext(conversation_id=cid, service=_Svc(), caps={})


class _OptInAgent(BaseReActAgent):
    """Opt-in agent (comops-style): contributes per-run attachment tools via
    _extra_run_tools, which is what re-points the shared agent on every request
    and makes run isolation matter in the first place."""

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


def _make_agent(create_agent_fn=None):
    """Minimal opt-in agent (no __init__) wired for the run-isolation path."""
    agent = _OptInAgent.__new__(_OptInAgent)
    agent._run_ctx = RunContext()
    agent.agent = None
    agent._active_tools = []
    agent._active_middleware = []
    agent._static_tools = []       # `tools` property returns [] without rebuild
    agent._static_middleware = []  # `middleware` property returns [] without rebuild
    agent._mcp_tools = None
    agent.selected_tool_names = []
    agent.agent_llm = None         # lacks get_num_tokens_* -> trimming skipped
    agent._create_agent = create_agent_fn or (
        lambda tools, middleware: _AgentStub(tools, middleware)
    )
    return agent


def test_interleaved_prepare_keeps_each_runs_locals_distinct():
    agent = _make_agent()

    _, agent_a, mem_a = agent._begin_run(history=[], vectorstore=None,
                                         attachment_tools_ctx=_ctx(1))
    # A second request prepares AFTER A captured its locals.
    _, agent_b, mem_b = agent._begin_run(history=[], vectorstore=None,
                                         attachment_tools_ctx=_ctx(2))

    # Each run captured its own agent + memory.
    assert agent_a is not agent_b
    assert mem_a is not mem_b

    # The shared instance now reflects the last writer (B), but A's captured
    # locals were NOT re-pointed to B — that is the isolation guarantee.
    assert agent.agent is agent_b
    assert agent.active_memory is mem_b
    assert agent_a is not agent.agent
    assert mem_a is not agent.active_memory


def test_tool_callback_records_into_its_own_runs_memory_under_concurrency():
    """A retriever tool firing during run A must record into A's memory even
    after run B started on another thread and re-pointed the shared instance.

    The read/output side already captures a per-run ``run_memory`` local, but
    the write-side callbacks (``_store_documents`` / ``_store_tool_input``)
    resolve the active memory at execution time. If that resolution reads a
    single shared reference, the last run to start wins: A's retrieved
    documents land in B's trace and A loses its own citations. Each run must
    resolve the memory of the run executing on THIS context.
    """
    agent = _make_agent()

    a_started = threading.Event()   # A has begun its run (memory active for A)
    b_started = threading.Event()   # B has begun and re-pointed the shared state
    a_recorded = threading.Event()  # A's tool callback finished
    captured = {}
    errors = {}

    def run_a():
        try:
            _, _, mem_a = agent._begin_run(history=[], vectorstore=None,
                                           attachment_tools_ctx=_ctx(1))
            captured["a"] = mem_a
            a_started.set()
            assert b_started.wait(timeout=5), "run B never started"
            # A's retriever tool fires now — AFTER B re-pointed the instance.
            agent._store_documents("retriever", [Document(page_content="A-doc",
                                                          metadata={"source": "A"})])
            a_recorded.set()
        except Exception as exc:  # pragma: no cover - surfaced via assert below
            errors["a"] = exc

    def run_b():
        try:
            assert a_started.wait(timeout=5), "run A never started"
            _, _, mem_b = agent._begin_run(history=[], vectorstore=None,
                                           attachment_tools_ctx=_ctx(2))
            captured["b"] = mem_b
            b_started.set()
        except Exception as exc:  # pragma: no cover - surfaced via assert below
            errors["b"] = exc

    t_a = threading.Thread(target=run_a)
    t_b = threading.Thread(target=run_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=5)
    t_b.join(timeout=5)

    assert not errors, errors
    assert a_recorded.is_set(), "A's tool callback never ran"
    mem_a, mem_b = captured["a"], captured["b"]
    assert mem_a is not mem_b
    a_docs = [d.page_content for d in mem_a.unique_documents()]
    b_docs = [d.page_content for d in mem_b.unique_documents()]
    assert a_docs == ["A-doc"], f"A's document should be in A's memory, got {a_docs}"
    assert b_docs == [], f"B's memory must not receive A's document, got {b_docs}"


def test_attachment_turn_compiles_agent_once():
    """An attachment turn must compile the LangGraph agent ONCE with the merged
    toolset — not once for the base build and again after adding the attachment
    tools. Per-run tools flow through _extra_run_tools into the single build."""
    compiles = []

    def counting_create(tools, middleware):
        compiles.append(list(tools))
        return _AgentStub(tools, middleware)

    agent = _make_agent(counting_create)
    agent._begin_run(history=[], vectorstore=None, attachment_tools_ctx=_ctx(1))
    assert len(compiles) == 1, f"expected 1 compile, got {len(compiles)}"
    # ...and the per-run attachment tools actually made it into that one build.
    assert len(compiles[0]) == 3


def test_two_threads_capture_own_run_under_lock():
    a_inside = threading.Event()      # A is mid-prepare, holding the lock
    a_may_finish = threading.Event()  # release A from inside _create_agent
    b_captured = threading.Event()    # B finished _begin_run
    first_call = {"seen": False}

    def create_agent_fn(tools, middleware):
        stub = _AgentStub(tools, middleware)
        # Only the first invocation (thread A) pauses inside the locked section.
        if not first_call["seen"]:
            first_call["seen"] = True
            a_inside.set()
            a_may_finish.wait(timeout=5)
        return stub

    agent = _make_agent(create_agent_fn)
    results = {}
    errors = {}

    def run(tag, cid):
        try:
            _, a, m = agent._begin_run(history=[], vectorstore=None,
                                       attachment_tools_ctx=_ctx(cid))
            results[tag] = (a, m)
            if tag == "B":
                b_captured.set()
        except Exception as exc:  # pragma: no cover - surfaced via assert below
            errors[tag] = exc

    t_a = threading.Thread(target=run, args=("A", 1))
    t_a.start()
    assert a_inside.wait(timeout=5), "thread A never entered its locked prepare"

    # A holds the lock; start B — it must block on _begin_run's lock.
    t_b = threading.Thread(target=run, args=("B", 2))
    t_b.start()
    assert not b_captured.wait(timeout=0.3), "B captured while A held the run lock"

    # Let A finish; B can now acquire the lock and capture.
    a_may_finish.set()
    t_a.join(timeout=5)
    t_b.join(timeout=5)

    assert not errors, errors
    agent_a, mem_a = results["A"]
    agent_b, mem_b = results["B"]
    assert agent_a is not agent_b
    assert mem_a is not mem_b
