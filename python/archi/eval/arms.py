"""Arms — answering configurations — and their registry.

An **arm** is one way of producing an answer to a QA atom: a raw LLM
call, an OKG deployment queried over MCP, a chat UI, a coding agent.
The engine runs atoms x arms and compares the arms in one report.
This abstraction is new in v3: the v2 PRs hard-wired a single tested
agent (PR #596's ``QAWorkflow`` run phase); the arm registry replaces
that with ``arm id -> factory(config)`` so new answering setups
register without touching the engine.

Contract (kept deliberately small):

- ``name`` — the registry id the instance answers as;
- ``describe()`` — one line for reports and ``list-arms``;
- ``answer(atom, ctx) -> AnswerRecord`` — answer text or error, plus
  optional latency / token / cost / generation-id fields.

Shipped arms:

- ``raw-llm`` — a no-context LLM baseline through a pluggable client
  (callable injection or a ``module:attr`` dotted path); no vendored
  provider SDK.
- ``okg-mcp`` — an OKG deployment answering over its MCP tools. The
  adapter seam (``invoke``) and config are in place; live session
  wiring is deliberately stubbed (raises :class:`NotConfiguredError`).
- ``openwebui-chat`` / ``codex`` — registered stubs with config
  schemas; their docstrings say what wiring they need.

Arms that raise inside ``answer`` produce ``execution_failed`` records
(the engine catches); a misconfigured arm raises
:class:`NotConfiguredError`, which the engine deliberately does *not*
catch — a setup problem should fail the run loudly, not score as
zeros (per the never-silent-fallback repo rule).
"""
from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol

from .atoms import QAAtom


class NotConfiguredError(RuntimeError):
    """The arm exists in the registry but is not wired up to run.

    The message must say exactly what is missing and how to provide it.
    """


@dataclass
class AnswerRecord:
    """One arm's answer to one atom (PR #596's ``AnswerAttempt``,
    flattened: no attempt ordinals or config digests, plus the v3
    token/cost/generation fields the report rolls up)."""

    atom_id: str
    arm: str
    answer: Optional[str] = None
    error: Optional[str] = None
    latency_ms: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    generation_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.answer is None) == (self.error is None):
            raise ValueError(
                "an AnswerRecord must carry exactly one of answer or error"
            )

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> Dict[str, Any]:
        raw: Dict[str, Any] = {"atom_id": self.atom_id, "arm": self.arm}
        for name in (
            "answer",
            "error",
            "latency_ms",
            "prompt_tokens",
            "completion_tokens",
            "cost_usd",
            "generation_id",
        ):
            value = getattr(self, name)
            if value is not None:
                raw[name] = value
        if self.extra:
            raw["extra"] = dict(self.extra)
        return raw


@dataclass(frozen=True)
class ArmContext:
    """Per-run context handed to every ``answer`` call."""

    run_id: str
    generation_id: Optional[str] = None


class Arm(Protocol):
    name: str

    def describe(self) -> str: ...

    def answer(self, atom: QAAtom, ctx: ArmContext) -> AnswerRecord: ...


# --- registry ---


@dataclass(frozen=True)
class ArmEntry:
    arm_id: str
    factory: Callable[[Mapping[str, Any]], Arm]
    summary: str
    config_keys: Dict[str, str]  # key -> one-line description


_REGISTRY: Dict[str, ArmEntry] = {}


def register_arm(
    arm_id: str, *, summary: str, config_keys: Optional[Dict[str, str]] = None
) -> Callable[[Callable[[Mapping[str, Any]], Arm]], Callable[[Mapping[str, Any]], Arm]]:
    """Register ``factory(config) -> Arm`` under ``arm_id``."""

    def decorator(
        factory: Callable[[Mapping[str, Any]], Arm],
    ) -> Callable[[Mapping[str, Any]], Arm]:
        if arm_id in _REGISTRY:
            raise ValueError(f"arm id '{arm_id}' is already registered")
        _REGISTRY[arm_id] = ArmEntry(
            arm_id=arm_id,
            factory=factory,
            summary=summary,
            config_keys=dict(config_keys or {}),
        )
        return factory

    return decorator


def create_arm(arm_id: str, config: Optional[Mapping[str, Any]] = None) -> Arm:
    entry = _REGISTRY.get(arm_id)
    if entry is None:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"unknown arm '{arm_id}'; registered arms: {known}")
    return entry.factory(config or {})


def list_arms() -> List[ArmEntry]:
    return [entry for _, entry in sorted(_REGISTRY.items())]


def _check_config(
    config: Mapping[str, Any], *, arm_id: str, allowed: Dict[str, str]
) -> None:
    unknown = sorted(set(config) - set(allowed))
    if unknown:
        raise ValueError(
            f"arm '{arm_id}' got unknown config key(s): {', '.join(unknown)}; "
            f"allowed: {', '.join(sorted(allowed))}"
        )


def _require(config: Mapping[str, Any], key: str, *, arm_id: str) -> Any:
    value = config.get(key)
    if value is None:
        raise NotConfiguredError(
            f"arm '{arm_id}' requires config key '{key}'"
        )
    return value


def _resolve_callable(value: Any, *, context: str) -> Callable[..., Any]:
    """Accept a callable directly or a ``module:attr`` dotted path.

    The dotted-path form is what makes injectable clients reachable
    from the CLI, where config files can only carry strings.
    """
    if callable(value):
        return value
    if isinstance(value, str) and ":" in value:
        module_name, _, attr = value.partition(":")
        try:
            resolved = getattr(importlib.import_module(module_name), attr)
        except (ImportError, AttributeError) as exc:
            raise NotConfiguredError(
                f"{context}: could not import '{value}' ({exc})"
            ) from exc
        if not callable(resolved):
            raise NotConfiguredError(f"{context}: '{value}' is not callable")
        return resolved
    raise NotConfiguredError(
        f"{context}: expected a callable or a 'module:attr' string"
    )


def _timed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1000))


def _record_from_reply(
    reply: Any, *, atom_id: str, arm: str, latency_ms: int
) -> AnswerRecord:
    """Normalize a client/adapter reply into an AnswerRecord.

    Accepted shapes: a plain string (the answer), or a mapping with
    ``answer`` plus optional ``prompt_tokens`` / ``completion_tokens``
    / ``cost_usd`` / ``generation_id`` / ``latency_ms``.
    """
    if isinstance(reply, str):
        return AnswerRecord(
            atom_id=atom_id, arm=arm, answer=reply, latency_ms=latency_ms
        )
    if isinstance(reply, Mapping):
        answer = reply.get("answer")
        if not isinstance(answer, str):
            raise ValueError("client reply mapping must carry a string 'answer'")
        known = {
            "answer",
            "prompt_tokens",
            "completion_tokens",
            "cost_usd",
            "generation_id",
            "latency_ms",
        }
        return AnswerRecord(
            atom_id=atom_id,
            arm=arm,
            answer=answer,
            latency_ms=reply.get("latency_ms", latency_ms),
            prompt_tokens=reply.get("prompt_tokens"),
            completion_tokens=reply.get("completion_tokens"),
            cost_usd=reply.get("cost_usd"),
            generation_id=reply.get("generation_id"),
            extra={key: value for key, value in reply.items() if key not in known},
        )
    raise ValueError("client reply must be a string or a mapping with 'answer'")


# --- shipped arms ---


RAW_LLM_CONFIG = {
    "model": "model identifier passed through to the client (required)",
    "client": (
        "answering client: a callable(question, *, model, system_prompt) or a "
        "'module:attr' dotted path to one (required)"
    ),
    "system_prompt": "optional system prompt prepended by the client",
}


@register_arm(
    "raw-llm",
    summary="no-context LLM baseline through a pluggable client (no SDK vendored)",
    config_keys=RAW_LLM_CONFIG,
)
class RawLLMArm:
    """Direct LLM call, no OKG context — the floor other arms must beat.

    The provider SDK is deliberately not vendored: the ``client``
    config key injects the call (tests pass a local callable; real use
    points a dotted path at a thin wrapper around the operator's
    provider client).
    """

    name = "raw-llm"

    def __init__(self, config: Mapping[str, Any]):
        _check_config(config, arm_id=self.name, allowed=RAW_LLM_CONFIG)
        self.model = _require(config, "model", arm_id=self.name)
        self.system_prompt = config.get("system_prompt")
        self._client = _resolve_callable(
            _require(config, "client", arm_id=self.name),
            context=f"arm '{self.name}' config key 'client'",
        )

    def describe(self) -> str:
        return f"raw LLM baseline (model={self.model}, no retrieval context)"

    def answer(self, atom: QAAtom, ctx: ArmContext) -> AnswerRecord:
        started = time.perf_counter()
        reply = self._client(
            atom.question, model=self.model, system_prompt=self.system_prompt
        )
        return _record_from_reply(
            reply, atom_id=atom.id, arm=self.name, latency_ms=_timed_ms(started)
        )


OKG_MCP_CONFIG = {
    "deployment": "OKG deployment name the questions target (required)",
    "dsn": "optional Postgres DSN of the deployment (cost rollups via okg.llm_calls)",
    "mcp_endpoint": "optional MCP endpoint URL of the deployment",
    "model": "optional model identifier the deployment's agent should use",
    "ask_tool": "MCP tool name that answers a question (default 'ask')",
    "invoke": (
        "adapter seam: a callable(tool, arguments) -> reply mapping, or a "
        "'module:attr' dotted path; absent -> NotConfiguredError"
    ),
}


@register_arm(
    "okg-mcp",
    summary="an OKG deployment answering over its MCP tools (live wiring stubbed)",
    config_keys=OKG_MCP_CONFIG,
)
class OKGMCPArm:
    """Answers via an OKG deployment's MCP tools.

    The adapter seam is ``invoke(tool, arguments) -> reply``: tests and
    early integrations inject it; the real MCP-session adapter (open an
    MCP client session against ``mcp_endpoint``, call ``ask_tool`` with
    the question, map the tool result to the reply shape, and surface
    the deployment's pinned generation id as ``generation_id``) is
    deliberately not implemented yet — without ``invoke`` this arm
    raises :class:`NotConfiguredError`.
    """

    name = "okg-mcp"

    def __init__(self, config: Mapping[str, Any]):
        _check_config(config, arm_id=self.name, allowed=OKG_MCP_CONFIG)
        self.deployment = _require(config, "deployment", arm_id=self.name)
        self.dsn = config.get("dsn")
        self.mcp_endpoint = config.get("mcp_endpoint")
        self.model = config.get("model")
        self.ask_tool = config.get("ask_tool", "ask")
        self._invoke = None
        if config.get("invoke") is not None:
            self._invoke = _resolve_callable(
                config["invoke"], context=f"arm '{self.name}' config key 'invoke'"
            )

    def describe(self) -> str:
        return f"OKG deployment '{self.deployment}' over MCP (tool '{self.ask_tool}')"

    def answer(self, atom: QAAtom, ctx: ArmContext) -> AnswerRecord:
        if self._invoke is None:
            raise NotConfiguredError(
                f"arm '{self.name}' has no live MCP wiring yet: inject an "
                "'invoke' callable (or dotted path) that opens an MCP session "
                f"against the deployment and calls its '{self.ask_tool}' tool"
            )
        started = time.perf_counter()
        reply = self._invoke(self.ask_tool, {"question": atom.question})
        return _record_from_reply(
            reply, atom_id=atom.id, arm=self.name, latency_ms=_timed_ms(started)
        )


OPENWEBUI_CONFIG = {
    "base_url": "OpenWebUI base URL, e.g. https://chat.example.org (required)",
    "api_key_env": "environment variable holding the API key (required; never a literal)",
    "model": "model/agent id exposed by the OpenWebUI instance (required)",
}


@register_arm(
    "openwebui-chat",
    summary="stub: an OpenWebUI chat deployment answering via its completions API",
    config_keys=OPENWEBUI_CONFIG,
)
class OpenWebUIChatArm:
    """Stub arm for an OpenWebUI chat frontend.

    Wiring needed to activate: POST ``{base_url}/api/chat/completions``
    with a bearer token read from ``api_key_env``, a single user
    message carrying the atom's question, and ``model``; map the reply
    text and usage fields onto :class:`AnswerRecord`. Until that
    adapter lands, ``answer`` raises :class:`NotConfiguredError`.
    """

    name = "openwebui-chat"

    def __init__(self, config: Mapping[str, Any]):
        _check_config(config, arm_id=self.name, allowed=OPENWEBUI_CONFIG)
        self.base_url = _require(config, "base_url", arm_id=self.name)
        self.api_key_env = _require(config, "api_key_env", arm_id=self.name)
        self.model = _require(config, "model", arm_id=self.name)

    def describe(self) -> str:
        return f"OpenWebUI chat at {self.base_url} (model={self.model}) [stub]"

    def answer(self, atom: QAAtom, ctx: ArmContext) -> AnswerRecord:
        raise NotConfiguredError(
            "arm 'openwebui-chat' is a registered stub: the completions-API "
            "adapter is not implemented yet (see the class docstring for the "
            "wiring it needs)"
        )


CODEX_CONFIG = {
    "command": "coding-agent executable to drive (default 'codex')",
    "workdir": "working directory the agent runs in (required)",
    "model": "optional model identifier passed to the agent",
}


@register_arm(
    "codex",
    summary="stub: a terminal coding agent answering via non-interactive exec",
    config_keys=CODEX_CONFIG,
)
class CodexArm:
    """Stub arm for a terminal coding agent (codex-style).

    Wiring needed to activate: run ``{command} exec`` (non-interactive)
    in ``workdir`` with the atom's question as the prompt, capture the
    final message as the answer, and parse token usage if the agent
    reports it. Until that subprocess adapter lands, ``answer`` raises
    :class:`NotConfiguredError`.
    """

    name = "codex"

    def __init__(self, config: Mapping[str, Any]):
        _check_config(config, arm_id=self.name, allowed=CODEX_CONFIG)
        self.command = config.get("command", "codex")
        self.workdir = _require(config, "workdir", arm_id=self.name)
        self.model = config.get("model")

    def describe(self) -> str:
        return f"coding agent '{self.command}' in {self.workdir} [stub]"

    def answer(self, atom: QAAtom, ctx: ArmContext) -> AnswerRecord:
        raise NotConfiguredError(
            "arm 'codex' is a registered stub: the non-interactive exec "
            "adapter is not implemented yet (see the class docstring for the "
            "wiring it needs)"
        )
