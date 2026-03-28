"""
Unit tests for A/B benchmarking helpers in service_benchmark.py
and the AB HTML report generator.
"""

import math
import sys
import os
import types
import pytest
from unittest.mock import MagicMock

# service_benchmark.py has heavy Docker-only deps (pandas, ragas, langchain, etc.)
# and module-level side effects. We stub all of them before importing.

_STUB_MODULES = [
    "pandas", "datasets", "yaml",
    "langchain_huggingface", "langchain_openai",
    "langchain_core", "langchain_core.messages",
    "ragas", "ragas.embeddings", "ragas.llms", "ragas.metrics",
    "src.archi.archi", "src.archi.pipelines.agents.agent_spec",
    "src.archi.providers",
    "src.utils.env", "src.utils.logging",
    "src.utils.postgres_service_factory",
]

for mod_name in _STUB_MODULES:
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
        # Add commonly accessed attrs
        if mod_name == "src.utils.env":
            stub.read_secret = lambda *a, **kw: "fake"
        elif mod_name == "src.utils.logging":
            stub.get_logger = lambda *a, **kw: MagicMock()
            stub.setup_logging = lambda *a, **kw: None
        elif mod_name == "src.utils.postgres_service_factory":
            factory_mock = MagicMock()
            factory_mock.from_env = MagicMock(return_value=MagicMock())
            factory_mock.set_instance = MagicMock()
            stub.PostgresServiceFactory = factory_mock
        elif mod_name == "ragas":
            stub.RunConfig = MagicMock()
            stub.evaluate = MagicMock()
        elif mod_name == "ragas.metrics":
            stub.answer_relevancy = MagicMock()
            stub.faithfulness = MagicMock()
            stub.context_precision = MagicMock()
            stub.context_recall = MagicMock()
        elif mod_name == "ragas.embeddings":
            stub.LangchainEmbeddingsWrapper = MagicMock()
        elif mod_name == "ragas.llms":
            stub.LangchainLLMWrapper = MagicMock()
        elif mod_name == "langchain_core.messages":
            stub.AIMessage = MagicMock()
            stub.HumanMessage = MagicMock()
            stub.ToolMessage = MagicMock()
        elif mod_name == "langchain_openai":
            stub.ChatOpenAI = MagicMock()
            stub.OpenAIEmbeddings = MagicMock()
        elif mod_name == "langchain_huggingface":
            stub.HuggingFaceEmbeddings = MagicMock()
        elif mod_name == "datasets":
            stub.Dataset = MagicMock()
        elif mod_name == "src.archi.archi":
            stub.archi = MagicMock()
        elif mod_name == "src.archi.pipelines.agents.agent_spec":
            stub.AgentSpecError = type("AgentSpecError", (Exception,), {})
            stub.load_agent_spec = MagicMock()
        elif mod_name == "src.archi.providers":
            stub.get_model = MagicMock()
        sys.modules[mod_name] = stub

# Also stub the generate_benchmark_report since it's imported at module level
# (we'll import the real one separately for our HTML tests)
_report_stub = types.ModuleType("src.utils.generate_benchmark_report")
_report_stub.parse_benchmark_results = MagicMock()
_report_stub.format_html_output = MagicMock(return_value="<html></html>")
_report_stub.format_ab_html_output = MagicMock(return_value="<html></html>")
sys.modules["src.utils.generate_benchmark_report"] = _report_stub

os.environ.setdefault("PG_PASSWORD", "fake")
os.environ.setdefault("PGHOST", "localhost")
os.environ.setdefault("PGPORT", "5432")
os.environ.setdefault("PGDATABASE", "test")
os.environ.setdefault("PGUSER", "test")

from src.bin.service_benchmark import ABResult, ResultHandler  # noqa: E402

# For the HTML tests, import the real function directly from the file
import importlib.util
_spec = importlib.util.spec_from_file_location(
    "generate_benchmark_report_real",
    os.path.join(os.path.dirname(__file__), "..", "..", "src", "utils", "generate_benchmark_report.py"),
)
_real_report_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_real_report_mod)
format_ab_html_output = _real_report_mod.format_ab_html_output

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_config_results(agent_class, provider, model, questions):
    """Return a single-config result dict shaped like ResultHandler.results items."""
    return {
        "single_question_results": questions,
        "total_results": {},
        "configuration_file": f"/tmp/{agent_class}.yaml",
        "configuration": {
            "services": {
                "benchmarking": {
                    "agent_class": agent_class,
                    "provider": provider,
                    "model": model,
                }
            }
        },
    }


def _sample_question(question, answer, relevancy=0.8, faithfulness=0.9, time=1.0):
    return {
        "question": question,
        "answer": answer,
        "reference_answer": "ref",
        "time_elapsed": time,
        "answer_relevancy": relevancy,
        "faithfulness": faithfulness,
        "sources_metadata": [],
        "messages": [],
    }


@pytest.fixture(autouse=True)
def _reset_result_handler():
    """Ensure ResultHandler class state is reset between tests."""
    ResultHandler.results = []
    ResultHandler.metadata = {}
    ResultHandler.ab_comparison = {}
    yield
    ResultHandler.results = []
    ResultHandler.metadata = {}
    ResultHandler.ab_comparison = {}


# ---------------------------------------------------------------------------
# ABResult dataclass
# ---------------------------------------------------------------------------

class TestABResult:
    def test_defaults(self):
        r = ABResult(
            question="q", reference_answer="a",
            answer_a="a1", answer_b="a2",
            time_a=1.0, time_b=2.0,
        )
        assert r.ragas_a == {}
        assert r.ragas_b == {}
        assert r.winner_by_metric == {}

    def test_with_metrics(self):
        r = ABResult(
            question="q", reference_answer="a",
            answer_a="a1", answer_b="a2",
            time_a=1.0, time_b=2.0,
            ragas_a={"answer_relevancy": 0.9},
            ragas_b={"answer_relevancy": 0.7},
            winner_by_metric={"answer_relevancy": "a"},
        )
        assert r.winner_by_metric["answer_relevancy"] == "a"


# ---------------------------------------------------------------------------
# pair_ab_results
# ---------------------------------------------------------------------------

class TestPairABResults:
    def test_basic_pairing(self):
        q_a = {"question_1": _sample_question("What?", "ans_a", relevancy=0.9, faithfulness=0.8)}
        q_b = {"question_1": _sample_question("What?", "ans_b", relevancy=0.7, faithfulness=0.85)}
        ResultHandler.results = [
            _make_config_results("AgentA", "openai", "gpt-4o", q_a),
            _make_config_results("AgentB", "local", "gemma3", q_b),
        ]

        paired = ResultHandler.pair_ab_results()
        assert len(paired) == 1
        assert paired[0].answer_a == "ans_a"
        assert paired[0].answer_b == "ans_b"
        assert paired[0].winner_by_metric["answer_relevancy"] == "a"
        assert paired[0].winner_by_metric["faithfulness"] == "b"

    def test_missing_question_in_b_skipped(self):
        q_a = {
            "question_1": _sample_question("Q1", "a1"),
            "question_2": _sample_question("Q2", "a2"),
        }
        q_b = {"question_1": _sample_question("Q1", "b1")}
        ResultHandler.results = [
            _make_config_results("A", "x", "x", q_a),
            _make_config_results("B", "x", "x", q_b),
        ]
        paired = ResultHandler.pair_ab_results()
        assert len(paired) == 1

    def test_raises_with_wrong_count(self):
        ResultHandler.results = [_make_config_results("A", "x", "x", {})]
        with pytest.raises(ValueError, match="exactly 2"):
            ResultHandler.pair_ab_results()

    def test_nan_scores_produce_tie(self):
        q_a = {"q1": _sample_question("Q", "a")}
        q_b = {"q1": _sample_question("Q", "b")}
        # Inject NaN
        q_a["q1"]["answer_relevancy"] = float("nan")
        q_b["q1"]["answer_relevancy"] = float("nan")
        ResultHandler.results = [
            _make_config_results("A", "x", "x", q_a),
            _make_config_results("B", "x", "x", q_b),
        ]
        paired = ResultHandler.pair_ab_results()
        assert paired[0].winner_by_metric["answer_relevancy"] == "tie"

    def test_tie_on_equal_scores(self):
        q_a = {"q1": _sample_question("Q", "a", relevancy=0.8)}
        q_b = {"q1": _sample_question("Q", "b", relevancy=0.8)}
        ResultHandler.results = [
            _make_config_results("A", "x", "x", q_a),
            _make_config_results("B", "x", "x", q_b),
        ]
        paired = ResultHandler.pair_ab_results()
        assert paired[0].winner_by_metric["answer_relevancy"] == "tie"


# ---------------------------------------------------------------------------
# dump_ab_comparison
# ---------------------------------------------------------------------------

class TestDumpABComparison:
    def test_aggregate_counts(self):
        q_a = {
            "q1": _sample_question("Q1", "a1", relevancy=0.9, faithfulness=0.5),
            "q2": _sample_question("Q2", "a2", relevancy=0.3, faithfulness=0.9),
        }
        q_b = {
            "q1": _sample_question("Q1", "b1", relevancy=0.7, faithfulness=0.8),
            "q2": _sample_question("Q2", "b2", relevancy=0.6, faithfulness=0.4),
        }
        ResultHandler.results = [
            _make_config_results("A", "openai", "gpt-4o", q_a),
            _make_config_results("B", "local", "gemma3", q_b),
        ]
        paired = ResultHandler.pair_ab_results()
        ResultHandler.dump_ab_comparison(paired)

        agg = ResultHandler.ab_comparison["aggregate"]
        total = agg["wins_a"] + agg["wins_b"] + agg["ties"]
        # 2 questions x 2 metrics = 4 metric comparisons
        assert total == 4
        assert len(ResultHandler.ab_comparison["per_question"]) == 2

    def test_config_metadata_populated(self):
        q = {"q1": _sample_question("Q", "a")}
        ResultHandler.results = [
            _make_config_results("AgentX", "openai", "gpt-4o", q),
            _make_config_results("AgentY", "local", "gemma3", q),
        ]
        paired = ResultHandler.pair_ab_results()
        ResultHandler.dump_ab_comparison(paired)
        assert ResultHandler.ab_comparison["config_a"]["agent_class"] == "AgentX"
        assert ResultHandler.ab_comparison["config_b"]["model"] == "gemma3"


# ---------------------------------------------------------------------------
# format_ab_html_output
# ---------------------------------------------------------------------------

class TestFormatABHTML:
    def test_produces_valid_html(self):
        ab_comparison = {
            "config_a": {"agent_class": "A", "provider": "openai", "model": "gpt-4o"},
            "config_b": {"agent_class": "B", "provider": "local", "model": "gemma3"},
            "per_question": [
                {
                    "question": "What is X?",
                    "reference_answer": "X is Y",
                    "answer_a": "Answer A",
                    "answer_b": "Answer B",
                    "time_a": 1.2,
                    "time_b": 2.3,
                    "ragas_a": {"answer_relevancy": 0.9},
                    "ragas_b": {"answer_relevancy": 0.7},
                    "winner_by_metric": {"answer_relevancy": "a"},
                },
            ],
            "aggregate": {
                "wins_a": 1,
                "wins_b": 0,
                "ties": 0,
                "mean_scores_a": {"answer_relevancy": 0.9},
                "mean_scores_b": {"answer_relevancy": 0.7},
            },
        }
        html_out = format_ab_html_output(ab_comparison)
        assert "<!DOCTYPE html>" in html_out
        assert "Config A" in html_out
        assert "Answer A" in html_out
        assert "Answer B" in html_out
        assert "gpt-4o" in html_out
        assert "gemma3" in html_out

    def test_empty_per_question(self):
        ab_comparison = {
            "config_a": {},
            "config_b": {},
            "per_question": [],
            "aggregate": {"wins_a": 0, "wins_b": 0, "ties": 0, "mean_scores_a": {}, "mean_scores_b": {}},
        }
        html_out = format_ab_html_output(ab_comparison)
        assert "Questions" in html_out
