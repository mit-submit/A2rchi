# isort: skip_file
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

import yaml

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

    def _load_vectorstore(self) -> Any:
        from src.archi.utils.vectorstore_connector import VectorstoreConnector

        return VectorstoreConnector(self.config).get_vectorstore()

    def run(self, question: str) -> str:
        chat = self.config["services"]["chat_app"]
        selected_tool_names = set(getattr(self.spec, "tools", []) or [])
        vectorstore = (
            self._load_vectorstore()
            if "search_vectorstore_hybrid" in selected_tool_names
            else None
        )
        pipeline = self.pipeline_class(
            config=deepcopy(self.config),
            agent_spec=deepcopy(self.spec),
            default_provider=chat["default_provider"],
            default_model=chat["default_model"],
        )
        if "mcp" in selected_tool_names and not pipeline.loaded_mcp_tools:
            raise RuntimeError(
                "agent spec selected 'mcp', but no MCP tools were loaded"
            )
        output = pipeline.invoke(
            history=[("User", question)],
            vectorstore=vectorstore,
        )
        answer = output.answer
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Archi produced no usable terminal answer")
        return answer
