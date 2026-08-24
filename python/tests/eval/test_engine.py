"""archi.eval.engine — scoring correctness + the run loop, offline."""
import pytest

from archi.eval.arms import NotConfiguredError, create_arm
from archi.eval.atoms import validate_atom
from archi.eval.engine import Judgment, evaluate_check, run_eval, score_gold_facts


def _atom(**overrides):
    raw = {
        "id": "q1",
        "question": "What?",
        "checks": [{"kind": "exact", "value": "42"}],
    }
    raw.update(overrides)
    return validate_atom({k: v for k, v in raw.items() if v is not None})


def _check(kind, value=None, case_sensitive=True, value_from=None):
    oracle = None
    if value_from is not None:
        oracle = {"kind": "mcp", "calls": [{"id": "c1", "tool": "t"}]}
    atom = _atom(
        checks=[
            {
                "kind": kind,
                **({"value": value} if value is not None else {}),
                **({"value_from": value_from} if value_from is not None else {}),
                "case_sensitive": case_sensitive,
            }
        ],
        oracle=oracle,
    )
    return atom.checks[0]


# --- deterministic checks ---


@pytest.mark.parametrize(
    "kind, value, case_sensitive, answer, expected",
    [
        ("exact", "42", True, "42", True),
        ("exact", "42", True, " 42 ", True),  # whitespace-insensitive
        ("exact", "42", True, "answer: 42", False),
        ("exact", "ABC", True, "abc", False),
        ("exact", "ABC", False, "abc", True),
        ("contains", "needle", True, "hay needle stack", True),
        ("contains", "needle", True, "hay stack", False),
        ("contains", "NeedLe", False, "the needle", True),
        ("contains", "NeedLe", True, "the needle", False),
        ("regex", r"CMSSW_\d+_\d+_\d+", True, "use CMSSW_14_0_7 now", True),
        ("regex", r"^exact$", True, "not exact here", False),
        ("regex", r"tier-2", False, "a Tier-2 site", True),
        ("regex", r"tier-2", True, "a Tier-2 site", False),
    ],
)
def test_evaluate_check_kinds(kind, value, case_sensitive, answer, expected):
    result = evaluate_check(_check(kind, value, case_sensitive), answer)
    assert result.passed is expected
    assert result.expected == value
    assert result.source == "literal"


def test_evaluate_check_value_from_resolves_and_stringifies():
    check = _check("contains", value_from="/c1/count")
    resolved = {"c1": {"count": 3}}
    result = evaluate_check(check, "There are 3 open downtimes.", resolved)
    assert result.passed and result.expected == "3" and result.source == "/c1/count"
    assert not evaluate_check(check, "There are 5.", resolved).passed
    with pytest.raises(ValueError, match="no resolved oracle answer"):
        evaluate_check(check, "any", None)
    with pytest.raises(ValueError, match="does not exist"):
        evaluate_check(check, "any", {"c1": {}})


# --- graded scoring (PR #596 math) ---


def _facts(*rows):
    atom = _atom(
        checks=None,
        gold_facts=[
            {"id": fact_id, "text": f"fact {fact_id}", "required": required}
            for fact_id, required in rows
        ],
    )
    return atom.gold_facts


def _judged(facts, outcomes):
    return [
        Judgment(fact_id=fact.id, outcome=outcome, rationale="r")
        for fact, outcome in zip(facts, outcomes)
    ]


def test_score_gold_facts_all_entailed():
    facts = _facts(("A1", True), ("A2", False))
    scored = score_gold_facts(facts, _judged(facts, ["entailed", "entailed"]))
    assert scored == {
        "atom_score": 1.0,
        "required_fact_recall": 1.0,
        "passed": True,
    }


def test_score_gold_facts_contradiction_clamps_at_zero():
    facts = _facts(("A1", True), ("A2", False), ("A3", False))
    scored = score_gold_facts(
        facts, _judged(facts, ["contradicted", "contradicted", "not_mentioned"])
    )
    assert scored["atom_score"] == 0.0  # max(0, -2/3)
    assert scored["required_fact_recall"] == 0.0
    assert scored["passed"] is False


def test_score_gold_facts_optional_miss_still_passes():
    facts = _facts(("A1", True), ("A2", False))
    scored = score_gold_facts(facts, _judged(facts, ["entailed", "not_mentioned"]))
    assert scored["passed"] is True
    assert scored["atom_score"] == 0.5
    assert scored["required_fact_recall"] == 1.0


def test_score_gold_facts_unjudgeable_counts_zero_and_fails_required():
    facts = _facts(("A1", True), ("A2", False))
    scored = score_gold_facts(facts, _judged(facts, ["unjudgeable", "entailed"]))
    assert scored["atom_score"] == 0.5
    assert scored["passed"] is False


@pytest.mark.parametrize(
    "outcomes_by_id, message",
    [
        ({"A1": "entailed"}, "missing judgment"),
        ({"A1": "entailed", "A2": "entailed", "AX": "entailed"}, "unknown gold fact"),
        ({"A1": "maybe", "A2": "entailed"}, "unsupported judgment outcome"),
    ],
)
def test_score_gold_facts_judgment_contract(outcomes_by_id, message):
    facts = _facts(("A1", True), ("A2", False))
    judgments = [
        Judgment(fact_id=fact_id, outcome=outcome, rationale="r")
        for fact_id, outcome in outcomes_by_id.items()
    ]
    with pytest.raises(ValueError, match=message):
        score_gold_facts(facts, judgments)


def test_score_gold_facts_duplicate_judgment_rejected():
    facts = _facts(("A1", True))
    judgments = _judged(facts, ["entailed"]) + _judged(facts, ["entailed"])
    with pytest.raises(ValueError, match="duplicate judgment"):
        score_gold_facts(facts, judgments)


# --- the run loop over the smoke fixture ---


def test_run_eval_all_pass(smoke_atoms, arm_cls, grader_cls, invoker_cls):
    grader = grader_cls()
    run = run_eval(
        smoke_atoms,
        [arm_cls(generation_id="gen:test")],
        grader=grader,
        oracle_invoker=invoker_cls(),
        dataset="qa_smoke.yaml",
    )
    assert len(run.arm_runs) == 1
    results = {result.atom_id: result for result in run.arm_runs[0].results}
    assert len(results) == 7
    assert all(result.status == "scored" for result in results.values())
    assert all(result.passed for result in results.values())
    live = results["live-open-downtimes"]
    assert live.live["pre_run_sha256"] == live.live["post_run_sha256"]
    assert run.generation_id == "gen:test"
    assert run.generation_conflict is False
    # The two atoms carrying gold facts actually consulted the grader.
    assert len(grader.calls) == 2


def test_run_eval_failing_answers_score_false(
    smoke_atoms, arm_cls, grader_cls, invoker_cls, answers
):
    answers.update(
        {
            "cmssw-latest-14x": "CMSSW_15_0_0",  # exact miss
            "dataset-path": "/JetMET0/Run2024A-v1/AOD",  # one of two checks
        }
    )
    run = run_eval(
        smoke_atoms,
        [arm_cls(answers)],
        grader=grader_cls(),
        oracle_invoker=invoker_cls(),
    )
    results = {result.atom_id: result for result in run.arm_runs[0].results}
    assert results["cmssw-latest-14x"].passed is False
    assert results["cmssw-latest-14x"].score == 0.0
    partial = results["dataset-path"]
    assert partial.passed is False and partial.score == 0.5
    assert results["t2-mit-storage"].passed is True


def test_run_eval_mixed_criteria_combines_checks_and_grading(
    smoke_atoms, arm_cls, grader_cls, invoker_cls
):
    grader = grader_cls(overrides={"B1": "contradicted"})
    run = run_eval(
        smoke_atoms, [arm_cls()], grader=grader, oracle_invoker=invoker_cls()
    )
    results = {result.atom_id: result for result in run.arm_runs[0].results}
    mixed = results["mixed-criteria"]
    assert mixed.status == "scored"
    assert mixed.passed is False  # checks pass, required fact contradicted
    assert mixed.score == 0.5  # mean(check part 1.0, graded part 0.0)
    assert mixed.required_fact_recall == 0.0


def test_run_eval_without_grader_marks_graded_only_atom_ungraded(
    smoke_atoms, arm_cls, invoker_cls
):
    run = run_eval(smoke_atoms, [arm_cls()], oracle_invoker=invoker_cls())
    results = {result.atom_id: result for result in run.arm_runs[0].results}
    assert results["xrootd-fallback"].status == "ungraded"
    assert results["xrootd-fallback"].passed is None
    mixed = results["mixed-criteria"]
    assert mixed.status == "scored" and "skipped" in mixed.detail
    assert mixed.passed is True  # checks-only pass


def test_run_eval_grader_blowup_is_evaluation_failed(
    smoke_atoms, arm_cls, invoker_cls
):
    class ExplodingGrader:
        def judge(self, question, answer, gold_facts):
            raise RuntimeError("judge unavailable")

    run = run_eval(
        smoke_atoms,
        [arm_cls()],
        grader=ExplodingGrader(),
        oracle_invoker=invoker_cls(),
    )
    results = {result.atom_id: result for result in run.arm_runs[0].results}
    assert results["xrootd-fallback"].status == "evaluation_failed"
    assert "judge unavailable" in results["xrootd-fallback"].detail


def test_run_eval_arm_exception_is_execution_failed(
    smoke_atoms, arm_cls, grader_cls, invoker_cls, answers
):
    answers.pop("gt-name-shape")
    run = run_eval(
        smoke_atoms,
        [arm_cls(answers)],
        grader=grader_cls(),
        oracle_invoker=invoker_cls(),
    )
    results = {result.atom_id: result for result in run.arm_runs[0].results}
    failed = results["gt-name-shape"]
    assert failed.status == "execution_failed"
    assert failed.passed is False
    assert "LookupError" in failed.record.error


def test_run_eval_not_configured_propagates(smoke_atoms):
    arm = create_arm("codex", {"workdir": "."})
    with pytest.raises(NotConfiguredError):
        run_eval(smoke_atoms, [arm])


# --- live-state atoms (PR #608 flow) ---


def test_live_atom_without_invoker_is_oracle_failed(
    smoke_atoms, arm_cls, grader_cls
):
    run = run_eval(smoke_atoms, [arm_cls()], grader=grader_cls())
    results = {result.atom_id: result for result in run.arm_runs[0].results}
    live = results["live-open-downtimes"]
    assert live.status == "oracle_failed"
    assert live.live == {"phase": "pre_run"}
    assert results["cmssw-latest-14x"].status == "scored"  # others unaffected


def test_live_atom_pre_run_oracle_failure_skips_the_arm(
    smoke_atoms, arm_cls, grader_cls, invoker_cls
):
    class CountingArm(arm_cls):
        def __init__(self):
            super().__init__()
            self.asked = []

        def answer(self, atom, ctx):
            self.asked.append(atom.id)
            return super().answer(atom, ctx)

    arm = CountingArm()
    run = run_eval(
        smoke_atoms,
        [arm],
        grader=grader_cls(),
        oracle_invoker=invoker_cls(fail_after=0),
    )
    results = {result.atom_id: result for result in run.arm_runs[0].results}
    assert results["live-open-downtimes"].status == "oracle_failed"
    assert "oracle backend went away" in results["live-open-downtimes"].detail
    assert "live-open-downtimes" not in arm.asked


def test_live_atom_post_run_failure_quarantines(
    smoke_atoms, arm_cls, grader_cls, invoker_cls
):
    run = run_eval(
        smoke_atoms,
        [arm_cls()],
        grader=grader_cls(),
        oracle_invoker=invoker_cls(fail_after=1),  # pre-run ok, post-run fails
    )
    results = {result.atom_id: result for result in run.arm_runs[0].results}
    live = results["live-open-downtimes"]
    assert live.status == "oracle_failed"
    assert live.live["phase"] == "post_run"
    assert live.record is not None  # the answer is kept for inspection


def test_live_atom_drift_is_answer_changed(
    smoke_atoms, arm_cls, grader_cls, invoker_cls
):
    class DriftingInvoker(invoker_cls):
        def invoke(self, call):
            self.calls += 1
            return {"summary": {"open_count": 3 if self.calls == 1 else 4}}

    run = run_eval(
        smoke_atoms,
        [arm_cls()],
        grader=grader_cls(),
        oracle_invoker=DriftingInvoker(),
    )
    results = {result.atom_id: result for result in run.arm_runs[0].results}
    live = results["live-open-downtimes"]
    assert live.status == "answer_changed"
    assert live.passed is None
    assert live.live["pre_run_sha256"] != live.live["post_run_sha256"]


def test_live_atom_scores_against_resolved_value(
    smoke_atoms, arm_cls, grader_cls, invoker_cls, answers
):
    answers["live-open-downtimes"] = "About 7 are open."
    run = run_eval(
        smoke_atoms,
        [arm_cls(answers)],
        grader=grader_cls(),
        oracle_invoker=invoker_cls(),
    )
    results = {result.atom_id: result for result in run.arm_runs[0].results}
    live = results["live-open-downtimes"]
    assert live.status == "scored" and live.passed is False
    assert live.check_results[0].expected == "3"


# --- generation pinning ---


def test_generation_pin_explicit_flag_wins(
    smoke_atoms, arm_cls, grader_cls, invoker_cls
):
    run = run_eval(
        smoke_atoms,
        [arm_cls(generation_id="gen:a")],
        grader=grader_cls(),
        oracle_invoker=invoker_cls(),
        generation_id="gen:pinned",
    )
    assert run.generation_id == "gen:pinned"
    assert run.generation_conflict is True  # explicit pin != observed


def test_generation_conflict_across_arms(
    smoke_atoms, arm_cls, grader_cls, invoker_cls
):
    run = run_eval(
        smoke_atoms,
        [
            arm_cls(generation_id="gen:a", name="arm-a"),
            arm_cls(generation_id="gen:b", name="arm-b"),
        ],
        grader=grader_cls(),
        oracle_invoker=invoker_cls(),
    )
    assert run.generation_id is None
    assert run.generation_conflict is True
    assert run.arm_runs[0].generation_ids == ("gen:a",)
    assert run.arm_runs[1].generation_ids == ("gen:b",)


def test_generation_absent_when_arms_report_none(
    smoke_atoms, arm_cls, grader_cls, invoker_cls
):
    run = run_eval(
        smoke_atoms, [arm_cls()], grader=grader_cls(), oracle_invoker=invoker_cls()
    )
    assert run.generation_id is None
    assert run.generation_conflict is False


def test_run_eval_requires_atoms_and_arms(smoke_atoms, arm_cls):
    with pytest.raises(ValueError, match="at least one atom"):
        run_eval([], [arm_cls()])
    with pytest.raises(ValueError, match="at least one arm"):
        run_eval(smoke_atoms, [])
