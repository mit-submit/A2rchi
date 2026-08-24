"""The eval engine: run atoms x arms, score, and collect results.

Scoring modes, in order of trust:

1. **Deterministic checks** (exact / contains / regex) — new in v3,
   first-class: no judge model, no ambiguity. A live atom's checks may
   pull their expected value out of the resolved oracle answer via
   ``value_from`` JSON pointers.
2. **Graded gold facts** — PR #596's judged mode, reduced to an
   injectable :class:`Grader` seam (its LLM comparator prompt is *not*
   wired here). The scoring math is #596's ``score_attempt`` verbatim:
   ``atom_score = max(0, sum(outcome values)/n)`` with entailed=1,
   not_mentioned=0, contradicted=-1; ``required_fact_recall``; passed
   iff every required fact is entailed. Deviation: a #596-legal
   ``unjudgeable`` outcome scores 0 here instead of KeyError-ing.

Live-state atoms follow PR #608's flow: resolve the oracle *before*
asking the arm, ask, resolve again *after*, and only score when the
canonical-JSON sha256 of the resolved answer is unchanged — otherwise
the result is quarantined as ``answer_changed``; a failing oracle
quarantines as ``oracle_failed``. The invoker is injectable (tests run
offline); a run over live atoms without an invoker quarantines them
instead of crashing.

Result lifecycle statuses (superset of #596's attempt lifecycle plus
#608's live reasons): ``scored``, ``execution_failed``,
``evaluation_failed``, ``ungraded``, ``oracle_failed``,
``answer_changed``.

Generation pinning: the run header records the OKG generation the
answers were produced against — an explicit pin passed by the caller
(``--generation`` in the CLI) wins; otherwise the engine collects the
distinct ``generation_id`` values the arms reported in their
AnswerRecords, pins a lone value, and flags a conflict when arms
disagree.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

from .arms import AnswerRecord, Arm, ArmContext, NotConfiguredError
from .atoms import (
    Check,
    GoldFact,
    OracleCall,
    OracleSpec,
    QAAtom,
    answer_sha256,
    canonical_json,
    resolve_json_pointer,
)

# #596 outcome values, plus unjudgeable -> 0 (see module docstring).
OUTCOME_VALUES = {
    "entailed": 1,
    "not_mentioned": 0,
    "contradicted": -1,
    "unjudgeable": 0,
}

RESULT_STATUSES = (
    "scored",
    "execution_failed",
    "evaluation_failed",
    "ungraded",
    "oracle_failed",
    "answer_changed",
)


# --- graded mode (interface ported from PR #596) ---


@dataclass(frozen=True)
class Judgment:
    """One graded verdict on one gold fact (PR #596 ``Judgment``)."""

    fact_id: str
    outcome: str
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fact_id": self.fact_id,
            "outcome": self.outcome,
            "rationale": self.rationale,
        }


class Grader(Protocol):
    """Injectable judge (PR #596's LLM comparator seam).

    Implementations return exactly one :class:`Judgment` per gold
    fact. The engine validates that contract and turns violations into
    ``evaluation_failed`` results rather than trusting the judge.
    """

    def judge(
        self, question: str, answer: str, gold_facts: Sequence[GoldFact]
    ) -> Sequence[Judgment]: ...


def _validate_judgments(
    judgments: Sequence[Judgment], gold_facts: Sequence[GoldFact]
) -> None:
    # Ported from #596 validate_judgments: one per fact, none unknown.
    gold_ids = {fact.id for fact in gold_facts}
    seen = set()
    for judgment in judgments:
        if judgment.outcome not in OUTCOME_VALUES:
            raise ValueError(f"unsupported judgment outcome '{judgment.outcome}'")
        if judgment.fact_id not in gold_ids:
            raise ValueError(
                f"judgment references unknown gold fact '{judgment.fact_id}'"
            )
        if judgment.fact_id in seen:
            raise ValueError(
                f"duplicate judgment for gold fact '{judgment.fact_id}'"
            )
        seen.add(judgment.fact_id)
    missing = sorted(gold_ids - seen)
    if missing:
        raise ValueError("missing judgment(s) for: " + ", ".join(missing))


def score_gold_facts(
    gold_facts: Sequence[GoldFact], judgments: Sequence[Judgment]
) -> Dict[str, Any]:
    """PR #596 ``score_attempt``, on validated judgments."""
    _validate_judgments(judgments, gold_facts)
    by_id = {judgment.fact_id: judgment for judgment in judgments}
    required = [fact for fact in gold_facts if fact.required]
    entailed_required = sum(
        1 for fact in required if by_id[fact.id].outcome == "entailed"
    )
    value_sum = sum(OUTCOME_VALUES[by_id[fact.id].outcome] for fact in gold_facts)
    return {
        "atom_score": max(0.0, value_sum / len(gold_facts)),
        "required_fact_recall": entailed_required / len(required),
        "passed": entailed_required == len(required),
    }


# --- deterministic checks (new in v3) ---


@dataclass(frozen=True)
class CheckResult:
    kind: str
    expected: str
    passed: bool
    source: str  # "literal" or the value_from pointer

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "expected": self.expected,
            "passed": self.passed,
            "source": self.source,
        }


def _expected_text(value: Any) -> str:
    """Render a resolved oracle value for text comparison."""
    if isinstance(value, str):
        return value
    return canonical_json(value)


def evaluate_check(
    check: Check, answer: str, resolved_answer: Optional[Mapping[str, Any]] = None
) -> CheckResult:
    if check.value is not None:
        expected = check.value
        source = "literal"
    else:
        if resolved_answer is None:
            raise ValueError(
                "check uses value_from but no resolved oracle answer is available"
            )
        expected = _expected_text(
            resolve_json_pointer(resolved_answer, check.value_from)
        )
        source = check.value_from
    haystack = answer
    needle = expected
    if check.kind == "regex":
        flags = 0 if check.case_sensitive else re.IGNORECASE
        passed = re.search(needle, haystack, flags) is not None
    else:
        if not check.case_sensitive:
            haystack = haystack.lower()
            needle = needle.lower()
        if check.kind == "exact":
            passed = haystack.strip() == needle.strip()
        else:  # contains
            passed = needle in haystack
    return CheckResult(kind=check.kind, expected=expected, passed=passed, source=source)


# --- live oracle resolution (ported from PR #608, mcp SDK dropped) ---


class OracleInvoker(Protocol):
    """Executes one oracle call and returns its JSON payload (a dict).

    The v2 code invoked MCP tools and normalized ``CallToolResult``;
    here the transport is injected so the engine (and tests) stay
    offline. A real invoker for the ``okg-mcp`` case opens an MCP
    session against the target deployment and calls ``call.tool`` with
    ``call.arguments``.
    """

    def invoke(self, call: OracleCall) -> Mapping[str, Any]: ...


class OracleResolutionError(RuntimeError):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class ResolvedOracle:
    answer: Dict[str, Any]  # call id -> selected data
    answer_sha256: str


def resolve_oracle(spec: OracleSpec, invoker: OracleInvoker) -> ResolvedOracle:
    answer: Dict[str, Any] = {}
    for call in spec.calls:
        try:
            payload = invoker.invoke(call)
        except Exception as exc:
            raise OracleResolutionError(
                f"oracle call '{call.id}' ({call.tool}) failed: {exc}"
            ) from exc
        if not isinstance(payload, Mapping) or not payload:
            raise OracleResolutionError(
                f"oracle call '{call.id}' returned a non-object or empty payload"
            )
        if call.answer_fields is None:
            answer[call.id] = dict(payload)
            continue
        selected = {}
        for name, pointer in call.answer_fields:
            try:
                selected[name] = resolve_json_pointer(payload, pointer)
            except ValueError as exc:
                raise OracleResolutionError(
                    f"oracle call '{call.id}' field '{name}': {exc}"
                ) from exc
        answer[call.id] = selected
    return ResolvedOracle(answer=answer, answer_sha256=answer_sha256(answer))


# --- results ---


@dataclass
class AtomResult:
    atom_id: str
    arm: str
    status: str
    passed: Optional[bool] = None
    score: Optional[float] = None
    check_results: Tuple[CheckResult, ...] = ()
    judgments: Tuple[Judgment, ...] = ()
    required_fact_recall: Optional[float] = None
    record: Optional[AnswerRecord] = None
    detail: Optional[str] = None
    live: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        raw: Dict[str, Any] = {
            "atom_id": self.atom_id,
            "arm": self.arm,
            "status": self.status,
        }
        if self.passed is not None:
            raw["passed"] = self.passed
        if self.score is not None:
            raw["score"] = self.score
        if self.check_results:
            raw["check_results"] = [result.to_dict() for result in self.check_results]
        if self.judgments:
            raw["judgments"] = [judgment.to_dict() for judgment in self.judgments]
        if self.required_fact_recall is not None:
            raw["required_fact_recall"] = self.required_fact_recall
        if self.record is not None:
            raw["record"] = self.record.to_dict()
        if self.detail is not None:
            raw["detail"] = self.detail
        if self.live is not None:
            raw["live"] = self.live
        return raw


@dataclass
class ArmRun:
    arm: str
    description: str
    results: List[AtomResult] = field(default_factory=list)
    generation_ids: Tuple[str, ...] = ()


@dataclass
class EvalRun:
    run_id: str
    started_at: str
    finished_at: str
    dataset: Optional[str]
    generation_id: Optional[str]
    generation_conflict: bool
    arm_runs: List[ArmRun]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def score_answer(
    atom: QAAtom,
    record: AnswerRecord,
    *,
    grader: Optional[Grader] = None,
    resolved_answer: Optional[Mapping[str, Any]] = None,
) -> AtomResult:
    """Score one successful answer against one atom's criteria."""
    assert record.answer is not None
    check_results = tuple(
        evaluate_check(check, record.answer, resolved_answer)
        for check in atom.checks
    )
    parts: List[float] = []
    passed_parts: List[bool] = []
    if check_results:
        parts.append(
            sum(1 for result in check_results if result.passed) / len(check_results)
        )
        passed_parts.append(all(result.passed for result in check_results))
    judgments: Tuple[Judgment, ...] = ()
    recall: Optional[float] = None
    graded_skipped = False
    if atom.gold_facts:
        if grader is None:
            graded_skipped = True
        else:
            try:
                judgments = tuple(
                    grader.judge(atom.question, record.answer, atom.gold_facts)
                )
                graded = score_gold_facts(atom.gold_facts, judgments)
            except Exception as exc:
                return AtomResult(
                    atom_id=atom.id,
                    arm=record.arm,
                    status="evaluation_failed",
                    record=record,
                    check_results=check_results,
                    detail=f"grader failed: {exc}",
                )
            parts.append(graded["atom_score"])
            passed_parts.append(graded["passed"])
            recall = graded["required_fact_recall"]
    if not parts:
        # Only graded criteria exist and no grader was injected.
        return AtomResult(
            atom_id=atom.id,
            arm=record.arm,
            status="ungraded",
            record=record,
            detail="atom has only gold_facts and no grader was injected",
        )
    detail = None
    if graded_skipped:
        detail = "gold_facts skipped (no grader injected); scored on checks only"
    return AtomResult(
        atom_id=atom.id,
        arm=record.arm,
        status="scored",
        passed=all(passed_parts),
        score=sum(parts) / len(parts),
        check_results=check_results,
        judgments=judgments,
        required_fact_recall=recall,
        record=record,
        detail=detail,
    )


def _run_atom(
    atom: QAAtom,
    arm: Arm,
    ctx: ArmContext,
    *,
    grader: Optional[Grader],
    oracle_invoker: Optional[OracleInvoker],
) -> AtomResult:
    resolved: Optional[ResolvedOracle] = None
    live: Optional[Dict[str, Any]] = None
    if atom.is_live:
        if oracle_invoker is None:
            return AtomResult(
                atom_id=atom.id,
                arm=arm.name,
                status="oracle_failed",
                detail="live atom but no oracle invoker was injected",
                live={"phase": "pre_run"},
            )
        try:
            resolved = resolve_oracle(atom.oracle, oracle_invoker)
        except OracleResolutionError as exc:
            return AtomResult(
                atom_id=atom.id,
                arm=arm.name,
                status="oracle_failed",
                detail=exc.detail,
                live={"phase": "pre_run"},
            )
        live = {"pre_run_sha256": resolved.answer_sha256}

    started = time.perf_counter()
    try:
        record = arm.answer(atom, ctx)
    except NotConfiguredError:
        raise  # setup problem: fail the run, never score it (see arms.py)
    except Exception as exc:
        record = AnswerRecord(
            atom_id=atom.id,
            arm=arm.name,
            error=f"{type(exc).__name__}: {exc}",
            latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
        )
    if not record.ok:
        return AtomResult(
            atom_id=atom.id,
            arm=arm.name,
            status="execution_failed",
            passed=False,
            record=record,
            detail=record.error,
            live=live,
        )

    if atom.is_live:
        # Post-run drift check (PR #608): only score when live state
        # held still for the whole ask.
        assert resolved is not None and live is not None
        try:
            post = resolve_oracle(atom.oracle, oracle_invoker)
        except OracleResolutionError as exc:
            live["phase"] = "post_run"
            return AtomResult(
                atom_id=atom.id,
                arm=arm.name,
                status="oracle_failed",
                record=record,
                detail=exc.detail,
                live=live,
            )
        live["post_run_sha256"] = post.answer_sha256
        if post.answer_sha256 != resolved.answer_sha256:
            return AtomResult(
                atom_id=atom.id,
                arm=arm.name,
                status="answer_changed",
                record=record,
                detail="live state changed between pre- and post-run oracle "
                "resolution; result quarantined",
                live=live,
            )

    result = score_answer(
        atom,
        record,
        grader=grader,
        resolved_answer=resolved.answer if resolved is not None else None,
    )
    result.live = live
    return result


def run_eval(
    atoms: Sequence[QAAtom],
    arms: Sequence[Arm],
    *,
    grader: Optional[Grader] = None,
    oracle_invoker: Optional[OracleInvoker] = None,
    generation_id: Optional[str] = None,
    dataset: Optional[str] = None,
    run_id: Optional[str] = None,
) -> EvalRun:
    """Run every atom against every arm and collect scored results."""
    if not atoms:
        raise ValueError("run_eval requires at least one atom")
    if not arms:
        raise ValueError("run_eval requires at least one arm")
    run_id = run_id or f"eval-{uuid.uuid4().hex[:12]}"
    started_at = _utc_now()
    ctx = ArmContext(run_id=run_id, generation_id=generation_id)
    arm_runs: List[ArmRun] = []
    observed: List[str] = []
    for arm in arms:
        arm_run = ArmRun(arm=arm.name, description=arm.describe())
        for atom in atoms:
            arm_run.results.append(
                _run_atom(
                    atom, arm, ctx, grader=grader, oracle_invoker=oracle_invoker
                )
            )
        arm_generations = sorted(
            {
                result.record.generation_id
                for result in arm_run.results
                if result.record is not None
                and result.record.generation_id is not None
            }
        )
        arm_run.generation_ids = tuple(arm_generations)
        for value in arm_generations:
            if value not in observed:
                observed.append(value)
        arm_runs.append(arm_run)
    conflict = len(observed) > 1 or (
        generation_id is not None and any(v != generation_id for v in observed)
    )
    pinned = generation_id if generation_id is not None else (
        observed[0] if len(observed) == 1 else None
    )
    return EvalRun(
        run_id=run_id,
        started_at=started_at,
        finished_at=_utc_now(),
        dataset=dataset,
        generation_id=pinned,
        generation_conflict=conflict,
        arm_runs=arm_runs,
    )
