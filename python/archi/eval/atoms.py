"""QA atom model + dataset loader for archi.eval.

An **atom** is one QA unit: a question plus the criteria a correct
answer must meet. Two variants share one class:

- a *static* atom carries literal criteria — deterministic ``checks``
  (exact / contains / regex) and/or ``gold_facts`` for the injectable
  LLM-graded mode;
- a *live-state* atom additionally carries an ``oracle`` recipe (from
  PR #608): MCP tool calls whose JSON results, selected via RFC 6901
  JSON pointers, form the expected answer at run time. Its checks may
  use ``value_from`` (a pointer into the resolved oracle answer)
  instead of a literal ``value``.

Provenance: the strict field validation style, gold-fact shape
(``id``/``text``/``required``, at least one required), and contextual
error messages port PR #596's ``src/evaluation/qa/validation.py``; the
oracle recipe shape, JSON-pointer validation/resolution, and
canonical-JSON hashing port PR #608's ``src/evaluation/qa/oracle.py``.
Changes: the v2 item fields (``time_sensitive``, ``answer_mode``,
``answer_source``, derived ids) are dropped — atoms are explicit-id
only, liveness is implied by the presence of ``oracle``; deterministic
``checks`` are new in v3; datasets load from JSON *or* YAML; the
``mcp`` SDK types are not imported (invokers hand the engine plain
JSON payloads); oracle calls' ``server`` is optional because a v3 run
targets one deployment.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

SCHEMA_VERSION = "archi-eval-v1"
CHECK_KINDS = ("exact", "contains", "regex")
ORACLE_KIND = "mcp"

ATOM_FIELDS = {"id", "question", "tags", "answer", "checks", "gold_facts", "oracle"}
CHECK_FIELDS = {"kind", "value", "value_from", "case_sensitive"}
GOLD_FACT_FIELDS = {"id", "text", "required"}
ORACLE_FIELDS = {"kind", "calls"}
ORACLE_CALL_FIELDS = {"id", "server", "tool", "arguments", "answer_fields"}


class DatasetError(ValueError):
    """A dataset file failed validation; the message says where and why."""


def _fail(context: str, message: str) -> None:
    raise DatasetError(f"{context}: {message}")


def _strict_keys(raw: Dict[str, Any], allowed: set, context: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        _fail(context, f"unknown field(s): {', '.join(unknown)}")


def _nonempty_string(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(context, "must be a non-empty string")
    if "\x00" in value:
        _fail(context, "must not contain NUL characters")
    return value


# --- JSON pointers + canonical JSON (ported from PR #608 oracle.py) ---


def validate_json_pointer(pointer: Any, context: str) -> str:
    """RFC 6901 syntax check: '' or '/...' with only ~0/~1 escapes."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        _fail(context, "must be an RFC 6901 JSON pointer")
    index = 0
    while index < len(pointer):
        if pointer[index] == "~":
            if index + 1 >= len(pointer) or pointer[index + 1] not in {"0", "1"}:
                _fail(context, "must be an RFC 6901 JSON pointer")
            index += 2
            continue
        index += 1
    return pointer


def resolve_json_pointer(value: Any, pointer: str) -> Any:
    validate_json_pointer(pointer, "JSON pointer")
    current = value
    if pointer == "":
        return deepcopy(current)
    for encoded in pointer[1:].split("/"):
        token = encoded.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                raise ValueError(f"JSON pointer '{pointer}' does not exist")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                raise ValueError(f"JSON pointer '{pointer}' does not exist")
            index = int(token)
            if index >= len(current):
                raise ValueError(f"JSON pointer '{pointer}' does not exist")
            current = current[index]
        else:
            raise ValueError(f"JSON pointer '{pointer}' does not exist")
    return deepcopy(current)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def answer_sha256(value: Dict[str, Any]) -> str:
    """Stable digest of a resolved oracle answer (PR #608 pinning)."""
    if not isinstance(value, dict) or not value:
        raise ValueError("answer data must be a non-empty object")
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# --- model ---


@dataclass(frozen=True)
class Check:
    """One deterministic expectation on the answer text.

    Exactly one of ``value`` (literal) / ``value_from`` (JSON pointer
    into a live atom's resolved oracle answer) is set.
    """

    kind: str
    value: Optional[str] = None
    value_from: Optional[str] = None
    case_sensitive: bool = True

    def to_dict(self) -> Dict[str, Any]:
        raw: Dict[str, Any] = {"kind": self.kind, "case_sensitive": self.case_sensitive}
        if self.value is not None:
            raw["value"] = self.value
        if self.value_from is not None:
            raw["value_from"] = self.value_from
        return raw


@dataclass(frozen=True)
class GoldFact:
    """One graded obligation (PR #596's gold atom, renamed: in v3 an
    'atom' is the QA unit, so the judged facts are gold *facts*)."""

    id: str
    text: str
    required: bool

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "text": self.text, "required": self.required}


@dataclass(frozen=True)
class OracleCall:
    """One MCP tool call in a live atom's oracle recipe (PR #608)."""

    id: str
    tool: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    answer_fields: Optional[Tuple[Tuple[str, str], ...]] = None
    server: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        raw: Dict[str, Any] = {
            "id": self.id,
            "tool": self.tool,
            "arguments": deepcopy(self.arguments),
        }
        if self.answer_fields is not None:
            raw["answer_fields"] = dict(self.answer_fields)
        if self.server is not None:
            raw["server"] = self.server
        return raw


@dataclass(frozen=True)
class OracleSpec:
    kind: str
    calls: Tuple[OracleCall, ...]

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "calls": [call.to_dict() for call in self.calls]}


@dataclass(frozen=True)
class QAAtom:
    id: str
    question: str
    tags: Tuple[str, ...] = ()
    answer: Optional[str] = None
    checks: Tuple[Check, ...] = ()
    gold_facts: Tuple[GoldFact, ...] = ()
    oracle: Optional[OracleSpec] = None

    @property
    def is_live(self) -> bool:
        return self.oracle is not None

    def to_dict(self) -> Dict[str, Any]:
        raw: Dict[str, Any] = {"id": self.id, "question": self.question}
        if self.tags:
            raw["tags"] = list(self.tags)
        if self.answer is not None:
            raw["answer"] = self.answer
        if self.checks:
            raw["checks"] = [check.to_dict() for check in self.checks]
        if self.gold_facts:
            raw["gold_facts"] = [fact.to_dict() for fact in self.gold_facts]
        if self.oracle is not None:
            raw["oracle"] = self.oracle.to_dict()
        return raw


# --- validation ---


def _validate_check(raw: Any, *, context: str, live: bool) -> Check:
    if not isinstance(raw, dict):
        _fail(context, "must be an object")
    _strict_keys(raw, CHECK_FIELDS, context)
    kind = raw.get("kind")
    if kind not in CHECK_KINDS:
        _fail(f"{context}.kind", f"must be one of: {', '.join(CHECK_KINDS)}")
    value = raw.get("value")
    value_from = raw.get("value_from")
    if (value is None) == (value_from is None):
        _fail(context, "must set exactly one of 'value' or 'value_from'")
    if value is not None:
        value = _nonempty_string(value, f"{context}.value")
        if kind == "regex":
            try:
                re.compile(value)
            except re.error as exc:
                _fail(f"{context}.value", f"is not a valid regex: {exc}")
    else:
        if not live:
            _fail(
                context,
                "'value_from' is only valid on a live atom (one with an 'oracle')",
            )
        validate_json_pointer(value_from, f"{context}.value_from")
    case_sensitive = raw.get("case_sensitive", True)
    if not isinstance(case_sensitive, bool):
        _fail(f"{context}.case_sensitive", "must be a boolean")
    return Check(
        kind=kind, value=value, value_from=value_from, case_sensitive=case_sensitive
    )


def _validate_gold_facts(raw: Any, *, context: str) -> Tuple[GoldFact, ...]:
    # Ported from PR #596 validate_atoms: unique ids, >=1 required.
    if not isinstance(raw, list) or not raw:
        _fail(context, "must be a non-empty list")
    facts = []
    seen = set()
    for index, row in enumerate(raw):
        fact_context = f"{context}[{index}]"
        if not isinstance(row, dict):
            _fail(fact_context, "must be an object")
        _strict_keys(row, GOLD_FACT_FIELDS, fact_context)
        fact_id = _nonempty_string(row.get("id"), f"{fact_context}.id")
        text = _nonempty_string(row.get("text"), f"{fact_context}.text")
        required = row.get("required")
        if not isinstance(required, bool):
            _fail(f"{fact_context}.required", "must be a boolean")
        if fact_id in seen:
            _fail(context, f"contains duplicate gold fact id '{fact_id}'")
        seen.add(fact_id)
        facts.append(GoldFact(id=fact_id, text=text, required=required))
    if not any(fact.required for fact in facts):
        _fail(context, "must contain at least one required gold fact")
    return tuple(facts)


def _validate_json_value(value: Any, context: str) -> None:
    if value is None or isinstance(value, (str, bool, int, float)):
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{context}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail(context, "object keys must be strings")
            _validate_json_value(item, f"{context}.{key}")
        return
    _fail(context, "must contain only JSON values")


def _validate_oracle(raw: Any, *, context: str) -> OracleSpec:
    # Ported from PR #608 parse_oracle_recipe (mcp SDK types dropped;
    # 'server' optional; 'metadata_fields' not carried over).
    if not isinstance(raw, dict):
        _fail(context, "must be an object")
    _strict_keys(raw, ORACLE_FIELDS, context)
    if raw.get("kind") != ORACLE_KIND:
        _fail(f"{context}.kind", f"must be '{ORACLE_KIND}'")
    raw_calls = raw.get("calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        _fail(f"{context}.calls", "must be a non-empty list")
    calls = []
    seen = set()
    for index, row in enumerate(raw_calls):
        call_context = f"{context}.calls[{index}]"
        if not isinstance(row, dict):
            _fail(call_context, "must be an object")
        _strict_keys(row, ORACLE_CALL_FIELDS, call_context)
        call_id = _nonempty_string(row.get("id"), f"{call_context}.id")
        if call_id in seen:
            _fail(context, f"contains duplicate call id '{call_id}'")
        seen.add(call_id)
        tool = _nonempty_string(row.get("tool"), f"{call_context}.tool")
        arguments = row.get("arguments", {})
        if not isinstance(arguments, dict):
            _fail(f"{call_context}.arguments", "must be an object")
        _validate_json_value(arguments, f"{call_context}.arguments")
        server = row.get("server")
        if server is not None:
            server = _nonempty_string(server, f"{call_context}.server")
        answer_fields = None
        if row.get("answer_fields") is not None:
            raw_fields = row["answer_fields"]
            if not isinstance(raw_fields, dict) or not raw_fields:
                _fail(f"{call_context}.answer_fields", "must be a non-empty object")
            answer_fields = tuple(
                (
                    _nonempty_string(name, f"{call_context}.answer_fields key"),
                    validate_json_pointer(
                        pointer, f"{call_context}.answer_fields.{name}"
                    ),
                )
                for name, pointer in raw_fields.items()
            )
        calls.append(
            OracleCall(
                id=call_id,
                tool=tool,
                arguments=deepcopy(arguments),
                answer_fields=answer_fields,
                server=server,
            )
        )
    return OracleSpec(kind=ORACLE_KIND, calls=tuple(calls))


def validate_atom(raw: Any, *, context: str = "atom") -> QAAtom:
    if not isinstance(raw, dict):
        _fail(context, "must be an object")
    _strict_keys(raw, ATOM_FIELDS, context)
    atom_id = _nonempty_string(raw.get("id"), f"{context}.id")
    context = f"{context} (id '{atom_id}')"
    question = _nonempty_string(raw.get("question"), f"{context}.question")
    raw_tags = raw.get("tags", [])
    if not isinstance(raw_tags, list):
        _fail(f"{context}.tags", "must be a list of strings")
    tags = tuple(
        _nonempty_string(tag, f"{context}.tags[{index}]")
        for index, tag in enumerate(raw_tags)
    )
    answer = raw.get("answer")
    if answer is not None:
        answer = _nonempty_string(answer, f"{context}.answer")
    oracle = None
    if raw.get("oracle") is not None:
        oracle = _validate_oracle(raw["oracle"], context=f"{context}.oracle")
    raw_checks = raw.get("checks", [])
    if not isinstance(raw_checks, list):
        _fail(f"{context}.checks", "must be a list")
    checks = tuple(
        _validate_check(
            row, context=f"{context}.checks[{index}]", live=oracle is not None
        )
        for index, row in enumerate(raw_checks)
    )
    gold_facts: Tuple[GoldFact, ...] = ()
    if raw.get("gold_facts") is not None:
        gold_facts = _validate_gold_facts(
            raw["gold_facts"], context=f"{context}.gold_facts"
        )
    if not checks and not gold_facts:
        _fail(
            context,
            "must define at least one scoring criterion "
            "('checks' and/or 'gold_facts')",
        )
    return QAAtom(
        id=atom_id,
        question=question,
        tags=tags,
        answer=answer,
        checks=checks,
        gold_facts=gold_facts,
        oracle=oracle,
    )


def validate_atoms(raw_rows: Any) -> Tuple[QAAtom, ...]:
    if not isinstance(raw_rows, list):
        raise DatasetError("dataset must contain a list of atoms")
    if not raw_rows:
        raise DatasetError("dataset must contain at least one atom")
    atoms = []
    seen = set()
    for index, raw in enumerate(raw_rows, 1):
        atom = validate_atom(raw, context=f"atom {index}")
        if atom.id in seen:
            raise DatasetError(f"dataset contains duplicate atom id '{atom.id}'")
        seen.add(atom.id)
        atoms.append(atom)
    return tuple(atoms)


# --- loading ---


def load_dataset(path: Path | str) -> Tuple[QAAtom, ...]:
    """Load and validate a JSON or YAML atom dataset.

    Accepted top-level shapes: a bare list of atoms, or an object with
    ``atoms`` (list) and an optional ``schema_version`` that must be
    ``archi-eval-v1`` when present.
    """
    path = Path(path)
    if not path.is_file():
        raise DatasetError(f"dataset must be an existing file: {path}")
    suffix = path.suffix.lower()
    if suffix not in {".json", ".yaml", ".yml"}:
        raise DatasetError("dataset must use .json, .yaml, or .yml")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise DatasetError("dataset must be UTF-8 encoded") from exc
    if suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"invalid JSON dataset: {exc}") from exc
    else:
        try:
            payload = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise DatasetError(f"invalid YAML dataset: {exc}") from exc
    if isinstance(payload, dict):
        _strict_keys(payload, {"schema_version", "atoms"}, "dataset")
        version = payload.get("schema_version")
        if version is not None and version != SCHEMA_VERSION:
            raise DatasetError(
                f"dataset.schema_version must be '{SCHEMA_VERSION}' (got {version!r})"
            )
        payload = payload.get("atoms")
    return validate_atoms(payload)
