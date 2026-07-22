"""RunContext packages the run lock plus atomic agent/memory capture that
keeps one shared pipeline instance safe under threaded request handling.

Direct tests: pure stdlib threading — no agent, LLM, Flask, or mocks.
"""

import threading

from src.archi.pipelines.agents.utils.run_context import RunContext
from src.archi.pipelines.agents.utils.run_memory import RunMemory


class _Owner:
    """Minimal stand-in for a pipeline composing a RunContext."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.agent = None

    def prepare(self, **kwargs):
        # Mirrors _prepare_agent_inputs: start a fresh memory, rebuild agent.
        self.ctx.start_memory()
        self.agent = object()
        return dict(kwargs)


def _capture(owner, **kwargs):
    return owner.ctx.capture(
        owner.prepare,
        get_agent_fn=lambda: owner.agent,
        refresh_fn=lambda: owner.agent,
        **kwargs,
    )


def test_interleaved_captures_keep_each_runs_locals_distinct():
    owner = _Owner(RunContext())

    inputs_a, agent_a, mem_a = _capture(owner, conversation=1)
    # A second request prepares AFTER A captured its locals.
    inputs_b, agent_b, mem_b = _capture(owner, conversation=2)

    assert inputs_a == {"conversation": 1}
    assert inputs_b == {"conversation": 2}
    assert agent_a is not agent_b
    assert mem_a is not mem_b

    # Shared state reflects the last writer (B), but A's captured locals
    # were NOT re-pointed to B — that is the isolation guarantee.
    assert owner.ctx.active_memory is mem_b
    assert owner.agent is agent_b


def test_second_capture_blocks_while_first_holds_the_lock():
    ctx = RunContext()
    a_inside = threading.Event()      # A is mid-prepare, holding the lock
    a_may_finish = threading.Event()  # release A from inside prepare
    b_captured = threading.Event()    # B finished capture
    first_call = {"seen": False}
    results = {}

    def prepare(tag):
        memory = ctx.start_memory()
        # Only the first invocation (thread A) pauses inside the locked section.
        if not first_call["seen"]:
            first_call["seen"] = True
            a_inside.set()
            a_may_finish.wait(timeout=5)
        return {"tag": tag, "memory": memory}

    def run(tag):
        _, _, mem = ctx.capture(
            lambda: prepare(tag),
            get_agent_fn=lambda: object(),
            refresh_fn=lambda: None,
        )
        results[tag] = mem
        if tag == "B":
            b_captured.set()

    t_a = threading.Thread(target=run, args=("A",))
    t_a.start()
    assert a_inside.wait(timeout=5), "thread A never entered its locked prepare"

    # A holds the lock; start B — it must block on capture's lock.
    t_b = threading.Thread(target=run, args=("B",))
    t_b.start()
    assert not b_captured.wait(timeout=0.3), "B captured while A held the run lock"

    # Let A finish; B can now acquire the lock and capture.
    a_may_finish.set()
    t_a.join(timeout=5)
    t_b.join(timeout=5)

    assert results["A"] is not results["B"]


def test_capture_refreshes_when_agent_is_none():
    ctx = RunContext()
    built = object()
    _, agent, mem = ctx.capture(
        lambda: {},
        get_agent_fn=lambda: None,
        refresh_fn=lambda: built,
    )
    assert agent is built
    assert mem is None  # prepare started no memory


def test_start_memory_routes_through_the_provided_factory():
    class _CustomMemory(RunMemory):
        pass

    ctx = RunContext()
    memory = ctx.start_memory(_CustomMemory)
    assert isinstance(memory, _CustomMemory)
    assert ctx.active_memory is memory
