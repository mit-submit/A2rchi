# isort: skip_file
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

import yaml
from langchain_core.callbacks import BaseCallbackHandler

from .constants import (  # isort: skip
    COMPARATOR_SYSTEM_PROMPT,
    GOLD_SYSTEM_PROMPT,
)
from .profile import EvaluatorProfile
from .validation import Atom

GOLD_ATOM_SCHEMA = {
    "title": "atoms",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "atoms": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "required": {"type": "boolean"},
                },
                "required": ["id", "text", "required"],
            },
        }
    },
    "required": ["atoms"],
}

JUDGMENT_SCHEMA = {
    "title": "judgments",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "judgments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "atom_id": {"type": "string"},
                    "outcome": {
                        "type": "string",
                        "enum": [
                            "entailed",
                            "not_mentioned",
                            "contradicted",
                            "unjudgeable",
                        ],
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["atom_id", "outcome", "rationale"],
            },
        }
    },
    "required": ["judgments"],
}


class ToolTimingCallback(BaseCallbackHandler):
    """Collect timing-only metadata for tool calls in one agent attempt."""

    run_inline = True

    def __init__(self) -> None:
        self._active: Dict[UUID, Tuple[int, str, float]] = {}
        self._next_ordinal = 1
        self.timings: List[Dict[str, Any]] = []

    def on_tool_start(
        self,
        serialized: Dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._active[run_id] = (
            self._next_ordinal,
            serialized["name"],
            perf_counter(),
        )
        self._next_ordinal += 1

    def _finish(self, run_id: UUID, status: str) -> None:
        ordinal, name, started_at = self._active.pop(run_id)
        self.timings.append(
            {
                "ordinal": ordinal,
                "name": name,
                "status": status,
                "duration_ms": max(
                    0,
                    int(round((perf_counter() - started_at) * 1000)),
                ),
            }
        )

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._finish(run_id, "success")

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> None:
        self._finish(run_id, "error")


class LangChainEvaluatorRuntime:
    def __init__(
        self,
        profile: EvaluatorProfile,
        model_factory: Optional[Callable[..., Any]] = None,
    ):
        if model_factory is None:
            from src.archi.providers import get_model

            model_factory = get_model
        self._models = {
            component: model_factory(
                descriptor.provider,
                descriptor.model,
                {},
                **descriptor.provider_kwargs(),
            )
            for component, descriptor in profile.components()
        }

    @staticmethod
    def _structured(
        model: Any, schema: Dict[str, Any], prompt: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        structured = model.with_structured_output(schema)
        result = structured.invoke(
            [
                ("system", prompt),
                ("human", json.dumps(payload, ensure_ascii=False, sort_keys=True)),
            ]
        )
        if hasattr(result, "model_dump"):
            result = result.model_dump()
        if not isinstance(result, dict):
            raise ValueError("structured evaluator returned a non-object")
        return result

    def extract_gold(self, question: str, answer: str) -> Dict[str, Any]:
        return self._structured(
            self._models["atoms_extractor"],
            GOLD_ATOM_SCHEMA,
            GOLD_SYSTEM_PROMPT,
            {"question": question, "answer": answer},
        )

    def compare(
        self,
        question: str,
        gold_atoms: Sequence[Atom],
        answer: str,
    ) -> Dict[str, Any]:
        return self._structured(
            self._models["evaluator"],
            JUDGMENT_SCHEMA,
            COMPARATOR_SYSTEM_PROMPT,
            {
                "question": question,
                "gold_atoms": [atom.to_dict() for atom in gold_atoms],
                "answer": answer,
            },
        )


def _validate_local_file(path: Path, suffixes: set, label: str) -> Path:
    raw = str(path)
    if raw == "-" or "://" in raw:
        raise ValueError(f"{label} must be a local file path")
    resolved = path.resolve()
    if not resolved.exists() or not resolved.is_file():
        raise ValueError(f"{label} must be an existing file: {path}")
    if resolved.suffix.lower() not in suffixes:
        raise ValueError(f"{label} must use one of: {', '.join(sorted(suffixes))}")
    return resolved


def load_agent_inputs(
    config_path: Path, spec_path: Path
) -> Tuple[Dict[str, Any], Any, str, type]:
    from src.archi.pipelines.agents.agent_spec import (
        AgentSpecError,
        load_agent_spec_from_text,
    )

    resolved_config_path = _validate_local_file(
        config_path, {".yaml", ".yml"}, "agent config"
    )
    resolved_spec_path = _validate_local_file(spec_path, {".md"}, "agent spec")
    try:
        config = yaml.safe_load(resolved_config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid agent config YAML: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError("agent config must be an object")
    services = config.get("services")
    chat = services.get("chat_app") if isinstance(services, dict) else None
    if not isinstance(chat, dict):
        raise ValueError("agent config requires services.chat_app")
    required = ("agent_class", "default_provider", "default_model")
    missing = [
        name
        for name in required
        if not isinstance(chat.get(name), str) or not chat[name].strip()
    ]
    if missing:
        raise ValueError(
            "agent config services.chat_app is missing non-empty field(s): "
            + ", ".join(missing)
        )
    spec_text = resolved_spec_path.read_text(encoding="utf-8")
    try:
        spec = replace(
            load_agent_spec_from_text(spec_text), source_path=resolved_spec_path
        )
    except (AgentSpecError, OSError) as exc:
        raise ValueError(f"invalid agent spec: {exc}") from exc
    pipelines = import_module("src.archi.pipelines")
    try:
        pipeline_class = getattr(pipelines, chat["agent_class"])
    except AttributeError as exc:
        raise ValueError(f"unknown agent class '{chat['agent_class']}'") from exc
    return config, spec, spec_text, pipeline_class


# TODO: Remove this evaluation-specific runtime once the generic `archi`
# runtime is refactored to initialize vector-store connections and other tool
# dependencies only when they are selected by the resolved agent config/spec.
# Until then, this adapter avoids creating dependencies that QA attempts do not
# need.
class ArchiAgentRuntime:
    def __init__(
        self,
        config: Dict[str, Any],
        spec: Any,
        pipeline_class: type,
    ):
        self.config = config
        self.spec = spec
        self.pipeline_class = pipeline_class
        self.tool_calls: List[Dict[str, Any]] = []
        self._pipeline: Optional[Any] = None
        self._vectorstore: Optional[Any] = None
        self._selected_tool_names = set(getattr(self.spec, "tools", []) or [])

    def _load_vectorstore(self) -> Any:
        from src.archi.utils.vectorstore_connector import VectorstoreConnector

        return VectorstoreConnector(self.config).get_vectorstore()

    def _runtime_for_attempt(self) -> Tuple[Any, Optional[Any]]:
        if self._pipeline is not None:
            return self._pipeline, self._vectorstore

        chat = self.config["services"]["chat_app"]
        vectorstore = (
            self._load_vectorstore()
            if "search_vectorstore_hybrid" in self._selected_tool_names
            else None
        )
        pipeline = self.pipeline_class(
            config=deepcopy(self.config),
            agent_spec=deepcopy(self.spec),
            default_provider=chat["default_provider"],
            default_model=chat["default_model"],
        )
        if "mcp" in self._selected_tool_names and not pipeline.loaded_mcp_tools:
            raise RuntimeError(
                "agent spec selected 'mcp', but no MCP tools were loaded"
            )

        # Cache only a completely initialized runtime. A failed initialization
        # remains retryable by the next independently accounted attempt.
        self._pipeline = pipeline
        self._vectorstore = vectorstore
        return pipeline, vectorstore

    def run(self, question: str) -> str:
        self.tool_calls = []
        timing_callback = ToolTimingCallback()
        pipeline, vectorstore = self._runtime_for_attempt()
        try:
            output = pipeline.invoke(
                history=[("User", question)],
                vectorstore=vectorstore,
                callbacks=[timing_callback],
            )
        finally:
            self.tool_calls = sorted(
                timing_callback.timings,
                key=lambda timing: timing["ordinal"],
            )
        answer = output.answer
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Archi produced no usable terminal answer")
        return answer
