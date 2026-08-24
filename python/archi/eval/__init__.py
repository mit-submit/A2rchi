"""archi.eval — the v3 evaluation framework (W5 foundation).

Runs QA *atoms* (question + expected criteria) against *arms* (answering
configurations: a raw LLM, an OKG deployment over MCP, a chat UI, ...),
scores the answers, and aggregates per-arm/per-atom reports with cost,
latency, and generation-pin rollups.

Provenance: restructured port of two v2-era PRs —

- PR #596 (``feat/archi-eval-command``): the atom-based QA engine. We
  keep its gold-fact judging model (entailed / not_mentioned /
  contradicted), scoring math (atom score, required-fact recall,
  pass = every required fact entailed), strict dataset validation with
  contextual errors, and the markdown report shape.
- PR #608 (``feat/live-eval``): live-state QA. We keep its MCP oracle
  recipe (tool calls + JSON-pointer field selection), canonical-JSON
  answer hashing, and the pre/post-run drift check (``oracle_failed`` /
  ``answer_changed``) with an injectable invoker.

What changed in the port: the v2 workspace/phase/manifest machinery,
click CLI, chat-app routes, and RBAC surfaces were not carried over;
the engine is a plain in-process run over an *arm registry* (new in
v3), deterministic checks (exact/contains/regex) are the first-class
scoring mode with the LLM-graded judge reduced to an injectable
interface, and nothing here imports the v2 tree, the ``mcp`` SDK, or
``okg`` itself.
"""

from .arms import AnswerRecord, Arm, ArmContext, NotConfiguredError  # noqa: F401
from .arms import create_arm, list_arms, register_arm  # noqa: F401
from .atoms import QAAtom, load_dataset  # noqa: F401
from .engine import EvalRun, run_eval  # noqa: F401
from .report import build_report, render_markdown  # noqa: F401
