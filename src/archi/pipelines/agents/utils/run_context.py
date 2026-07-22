"""Per-run concurrency isolation for pipeline instances shared across threads."""

from __future__ import annotations

import contextvars
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from src.archi.pipelines.agents.utils.run_memory import RunMemory


class RunContext:
    """Own the run lock and active-memory reference for a shared pipeline.

    A single pipeline instance serves every request thread (Flask
    ``threaded=True``), so per-run state hung off that instance is re-pointed
    by each concurrent run. RunContext packages the isolation concern: hold
    one lock across input preparation and the capture of the run's agent and
    memory, so run bodies work off the returned locals and a second request
    cannot swap the references a run reads mid-stream.

    The active memory is stored in a :class:`contextvars.ContextVar`, not a
    plain attribute, because tool callbacks (``_store_documents`` /
    ``_store_tool_input``) resolve it at execution time, deep inside the run.
    A shared attribute would return whichever run most recently called
    :meth:`start_memory` — so a concurrent request would capture the first
    run's retrieved documents into its own trace. A ContextVar isolates the
    reference per execution context: a distinct thread for each synchronous
    request, a distinct task for each async stream, and it is copied into
    executor threads, so sync tools, async streams, and thread-pool tool
    calls all record into the memory of the run they belong to.

    Adopt by composition: hold a ``RunContext()``, route per-run memory
    creation through :meth:`start_memory`, and open each run with
    :meth:`capture`.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Per-instance ContextVar: one pipeline (hence one RunContext) is a
        # process-lifetime singleton, so this is not the repeatedly-created
        # ContextVar the stdlib warns leaks.
        self._active_memory: "contextvars.ContextVar[Optional[RunMemory]]" = (
            contextvars.ContextVar("archi_run_active_memory", default=None)
        )

    @property
    def active_memory(self) -> Optional[RunMemory]:
        """Return the memory bound to the run on the current context, if any."""
        return self._active_memory.get()

    def start_memory(self, factory: Callable[[], RunMemory] = RunMemory) -> RunMemory:
        """Create a fresh run memory via ``factory`` and bind it to this context."""
        memory = factory()
        self._active_memory.set(memory)
        return memory

    def capture(
        self,
        prepare_fn: Callable[..., Dict[str, Any]],
        *,
        get_agent_fn: Callable[[], Any],
        refresh_fn: Callable[[], Any],
        **prepare_kwargs: Any,
    ) -> Tuple[Dict[str, Any], Any, Optional[RunMemory]]:
        """Prepare inputs and atomically capture this run's agent and memory.

        Runs ``prepare_fn`` under the lock (its side effects typically start
        the active memory via :meth:`start_memory` and rebuild the agent),
        then captures the current agent from ``get_agent_fn`` — falling back
        to ``refresh_fn`` when it is ``None`` — together with the active
        memory. Callers must execute against the RETURNED locals; the shared
        attributes may be re-pointed by the next run.
        """
        with self._lock:
            agent_inputs = prepare_fn(**prepare_kwargs)
            agent = get_agent_fn()
            run_memory = self._active_memory.get()
            if agent is None:
                agent = refresh_fn()
        return agent_inputs, agent, run_memory
