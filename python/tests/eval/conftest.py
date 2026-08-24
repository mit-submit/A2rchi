"""Shared fakes for the archi.eval tests: a scripted arm, a scripted
grader, and a scripted oracle invoker — no network, no okg, no LLM.

Exposed as fixtures (the repo's test tree carries no ``__init__.py``,
so tests take these as arguments rather than importing them).
"""
from pathlib import Path

import pytest

from archi.eval.arms import AnswerRecord
from archi.eval.atoms import load_dataset
from archi.eval.engine import Judgment

FIXTURES = Path(__file__).parent / "fixtures"

# Answers that satisfy every atom in qa_smoke.yaml, given the fake
# grader judges everything entailed and the fake oracle reports 3 open
# downtimes.
GOOD_ANSWERS = {
    "cmssw-latest-14x": "CMSSW_14_0_7",
    "t2-mit-storage": "The site is served by SE01.CMSAF.MIT.EDU.",
    "gt-name-shape": "140X_dataRun3_Prompt_v4 is active for prompt reco.",
    "dataset-path": "/JetMET0/Run2024A-v1/RAW",
    "xrootd-fallback": "On a failed local open the job retries via the "
    "global xrootd redirector and streams from another site.",
    "mixed-criteria": "It is a Tier-2 site operated by MIT.",
    "live-open-downtimes": "There are 3 open downtimes.",
}


class FakeArm:
    """Deterministic arm answering from a lookup table."""

    def __init__(
        self,
        answers=None,
        *,
        name="fake",
        generation_id=None,
        latency_ms=10,
        cost_usd=None,
        prompt_tokens=None,
        completion_tokens=None,
    ):
        self.name = name
        self.answers = dict(GOOD_ANSWERS if answers is None else answers)
        self.generation_id = generation_id
        self.latency_ms = latency_ms
        self.cost_usd = cost_usd
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens

    def describe(self):
        return "scripted test arm"

    def answer(self, atom, ctx):
        if atom.id not in self.answers:
            raise LookupError(f"no scripted answer for {atom.id}")
        return AnswerRecord(
            atom_id=atom.id,
            arm=self.name,
            answer=self.answers[atom.id],
            latency_ms=self.latency_ms,
            generation_id=self.generation_id,
            cost_usd=self.cost_usd,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
        )


class FakeGrader:
    """Judges every gold fact with a fixed outcome (default entailed)."""

    def __init__(self, outcome="entailed", overrides=None):
        self.outcome = outcome
        self.overrides = overrides or {}
        self.calls = []

    def judge(self, question, answer, gold_facts):
        self.calls.append((question, answer))
        return [
            Judgment(
                fact_id=fact.id,
                outcome=self.overrides.get(fact.id, self.outcome),
                rationale="scripted",
            )
            for fact in gold_facts
        ]


class FakeInvoker:
    """Oracle invoker returning a scripted payload per tool name."""

    def __init__(self, payloads=None, fail_after=None):
        self.payloads = payloads or {
            "archi_gocdb_open_downtimes": {"summary": {"open_count": 3}}
        }
        self.calls = 0
        self.fail_after = fail_after  # fail once call count exceeds this

    def invoke(self, call):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError("oracle backend went away")
        return self.payloads[call.tool]


@pytest.fixture(scope="session")
def smoke_dataset():
    return FIXTURES / "qa_smoke.yaml"


@pytest.fixture
def smoke_atoms(smoke_dataset):
    return load_dataset(smoke_dataset)


@pytest.fixture
def answers():
    return dict(GOOD_ANSWERS)


@pytest.fixture
def arm_cls():
    return FakeArm


@pytest.fixture
def grader_cls():
    return FakeGrader


@pytest.fixture
def invoker_cls():
    return FakeInvoker
