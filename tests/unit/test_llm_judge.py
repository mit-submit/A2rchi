"""
Unit tests for LLM-as-Judge evaluation in service_benchmark.py.
"""

import json
import math
import os
import sys
import types
import pytest
from dataclasses import asdict
from unittest.mock import MagicMock, patch, call

# ── Stub heavy / Docker-only dependencies ─────────────────────────────
_STUB_MODULES = [
    "datasets", "yaml",
    "langchain_huggingface", "langchain_openai",
    "langchain_core", "langchain_core.messages",
    "ragas", "ragas.embeddings", "ragas.llms", "ragas.metrics",
    "src.archi.archi", "src.archi.pipelines.agents.agent_spec",
    "src.archi.providers",
    "src.utils.env", "src.utils.logging",
    "src.utils.postgres_service_factory",
    "openai",
]

for mod_name in _STUB_MODULES:
    if mod_name not in sys.modules:
        stub = types.ModuleType(mod_name)
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
            stub.ContextPrecision = MagicMock()
            stub.ContextRecall = MagicMock()
            stub.Faithfulness = MagicMock()
            stub.ResponseRelevancy = MagicMock()
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
        elif mod_name == "openai":
            stub.OpenAI = MagicMock()
        sys.modules[mod_name] = stub

# Restore real pandas — it is installed and needed for DataFrame operations in tests
import pandas as _real_pandas
sys.modules["pandas"] = _real_pandas

os.environ.setdefault("PG_PASSWORD", "fake")
os.environ.setdefault("PGHOST", "localhost")
os.environ.setdefault("PGPORT", "5432")
os.environ.setdefault("PGDATABASE", "test")
os.environ.setdefault("PGUSER", "test")

from src.bin.service_benchmark import ABResult, Benchmarker, _result_handler  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────

def _make_benchmarker(**overrides):
    """Create a Benchmarker without __init__ side-effects."""
    b = Benchmarker.__new__(Benchmarker)
    b.benchmarking_configs = overrides.get("benchmarking_configs", {
        "mode_settings": {
            "llm_judge_settings": {
                "evaluator_model": "test-model",
                "dimensions": ["correctness", "completeness", "relevance", "helpfulness"],
                "pairwise": True,
            }
        }
    })
    return b


def _mock_openai_response(content_dict):
    """Build a mock OpenAI ChatCompletion response."""
    choice = MagicMock()
    choice.message.content = json.dumps(content_dict)
    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ── ABResult dataclass fields ─────────────────────────────────────────

class TestABResultLLMJudgeFields:
    def test_defaults_empty(self):
        r = ABResult(
            question="q", reference_answer="a",
            answer_a="a1", answer_b="a2",
            time_a=1.0, time_b=2.0,
        )
        assert r.llm_judge_a == {}
        assert r.llm_judge_b == {}
        assert r.llm_judge_pairwise == {}

    def test_with_judge_scores(self):
        r = ABResult(
            question="q", reference_answer="a",
            answer_a="a1", answer_b="a2",
            time_a=1.0, time_b=2.0,
            llm_judge_a={"correctness": 5, "completeness": 4},
            llm_judge_b={"correctness": 3, "completeness": 2},
            llm_judge_pairwise={"final_winner": "a"},
        )
        assert r.llm_judge_a["correctness"] == 5
        assert r.llm_judge_b["correctness"] == 3
        assert r.llm_judge_pairwise["final_winner"] == "a"

    def test_serializes_to_dict(self):
        r = ABResult(
            question="q", reference_answer="a",
            answer_a="a1", answer_b="a2",
            time_a=1.0, time_b=2.0,
            llm_judge_pairwise={"final_winner": "tie"},
        )
        d = asdict(r)
        assert d["llm_judge_pairwise"]["final_winner"] == "tie"


# ── Prompt building ──────────────────────────────────────────────────

class TestPromptBuilding:
    def test_absolute_prompt_includes_rubric(self):
        b = _make_benchmarker()
        prompt = b._build_absolute_prompt(
            ["correctness", "helpfulness"],
            "What is X?",
            "X is Y",
            "X is Z",
        )
        assert "Correctness" in prompt
        assert "Helpfulness" in prompt
        assert "Completeness" not in prompt  # not in requested dims
        assert "What is X?" in prompt
        assert "X is Y" in prompt
        assert "X is Z" in prompt

    def test_absolute_prompt_all_dimensions(self):
        b = _make_benchmarker()
        prompt = b._build_absolute_prompt(
            ["correctness", "completeness", "relevance", "helpfulness"],
            "Q", "R", "A",
        )
        for dim in ["Correctness", "Completeness", "Relevance", "Helpfulness"]:
            assert dim in prompt

    def test_pairwise_prompt_structure(self):
        b = _make_benchmarker()
        prompt = b._build_pairwise_prompt("What?", "Answer A text", "Answer B text")
        assert "Response A:" in prompt
        assert "Response B:" in prompt
        assert "Answer A text" in prompt
        assert "Answer B text" in prompt
        assert '"winner"' in prompt


# ── _call_llm_judge ──────────────────────────────────────────────────

class TestCallLLMJudge:
    def test_parses_json_response(self):
        b = _make_benchmarker()
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_openai_response(
            {"correctness": 4, "reasoning": "good"}
        )
        result = b._call_llm_judge(client, "test-model", "prompt text")
        assert result["correctness"] == 4
        assert result["reasoning"] == "good"

    def test_passes_correct_params(self):
        b = _make_benchmarker()
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_openai_response({"ok": True})
        b._call_llm_judge(client, "my-model", "test prompt", max_tokens=512)

        call_kwargs = client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "my-model"
        assert call_kwargs["max_completion_tokens"] == 512
        assert call_kwargs["response_format"] == {"type": "json_object"}
        assert len(call_kwargs["messages"]) == 2
        assert call_kwargs["messages"][1]["content"] == "test prompt"


# ── get_llm_judge_results (absolute scoring) ─────────────────────────

class TestGetLLMJudgeResults:
    def test_scores_written_to_question_data(self):
        b = _make_benchmarker()
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_openai_response(
            {"correctness": 5, "completeness": 4, "relevance": 3, "helpfulness": 2, "reasoning": "test"}
        )
        with patch.object(b, "_get_llm_judge_client", return_value=client):
            qwr = {
                "question_1": {
                    "question": "What?",
                    "reference_answer": "Ref",
                    "answer": "Ans",
                },
            }
            df = b.get_llm_judge_results(qwr)

        assert qwr["question_1"]["llm_judge_correctness"] == 5
        assert qwr["question_1"]["llm_judge_completeness"] == 4
        assert qwr["question_1"]["llm_judge_relevance"] == 3
        assert qwr["question_1"]["llm_judge_helpfulness"] == 2
        assert qwr["question_1"]["llm_judge_reasoning"] == "test"
        assert len(df) == 1
        assert df["correctness"].iloc[0] == 5

    def test_multiple_questions(self):
        b = _make_benchmarker()
        scores_iter = iter([
            {"correctness": 5, "completeness": 5, "relevance": 5, "helpfulness": 5, "reasoning": "a"},
            {"correctness": 3, "completeness": 3, "relevance": 3, "helpfulness": 3, "reasoning": "b"},
        ])
        client = MagicMock()
        client.chat.completions.create.side_effect = lambda **kw: _mock_openai_response(next(scores_iter))

        with patch.object(b, "_get_llm_judge_client", return_value=client):
            qwr = {
                "question_1": {"question": "Q1", "reference_answer": "R1", "answer": "A1"},
                "question_2": {"question": "Q2", "reference_answer": "R2", "answer": "A2"},
            }
            df = b.get_llm_judge_results(qwr)

        assert len(df) == 2
        assert df["correctness"].mean() == 4.0

    def test_api_error_records_nan(self):
        b = _make_benchmarker()
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("API down")

        with patch.object(b, "_get_llm_judge_client", return_value=client):
            qwr = {
                "question_1": {"question": "Q1", "reference_answer": "R1", "answer": "A1"},
            }
            df = b.get_llm_judge_results(qwr)

        assert math.isnan(qwr["question_1"]["llm_judge_correctness"])
        assert math.isnan(df["correctness"].iloc[0])

    def test_custom_dimensions(self):
        b = _make_benchmarker(benchmarking_configs={
            "mode_settings": {
                "llm_judge_settings": {
                    "evaluator_model": "test-model",
                    "dimensions": ["correctness", "relevance"],
                }
            }
        })
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_openai_response(
            {"correctness": 4, "relevance": 5, "reasoning": "ok"}
        )
        with patch.object(b, "_get_llm_judge_client", return_value=client):
            qwr = {
                "question_1": {"question": "Q", "reference_answer": "R", "answer": "A"},
            }
            df = b.get_llm_judge_results(qwr)

        assert set(df.columns) == {"correctness", "relevance"}
        assert "llm_judge_completeness" not in qwr["question_1"]


# ── get_llm_judge_pairwise ───────────────────────────────────────────

class TestGetLLMJudgePairwise:
    def _make_paired(self, n=1):
        return [
            ABResult(
                question=f"Q{i}", reference_answer=f"R{i}",
                answer_a=f"A{i}", answer_b=f"B{i}",
                time_a=1.0, time_b=1.0,
            )
            for i in range(n)
        ]

    def test_agreement_a_wins(self):
        b = _make_benchmarker()
        # Both orders say A is better
        responses = iter([
            {"winner": "A", "reasoning": "A better"},  # A-first pass
            {"winner": "B", "reasoning": "second was better"},  # B-first pass → maps to A
        ])
        client = MagicMock()
        client.chat.completions.create.side_effect = lambda **kw: _mock_openai_response(next(responses))

        with patch.object(b, "_get_llm_judge_client", return_value=client):
            paired = self._make_paired(1)
            b.get_llm_judge_pairwise(paired)

        assert paired[0].llm_judge_pairwise["final_winner"] == "a"
        assert paired[0].winner_by_metric["llm_judge"] == "a"

    def test_agreement_b_wins(self):
        b = _make_benchmarker()
        responses = iter([
            {"winner": "B", "reasoning": "B better"},  # A-first pass
            {"winner": "A", "reasoning": "first was better"},  # B-first pass → maps to B
        ])
        client = MagicMock()
        client.chat.completions.create.side_effect = lambda **kw: _mock_openai_response(next(responses))

        with patch.object(b, "_get_llm_judge_client", return_value=client):
            paired = self._make_paired(1)
            b.get_llm_judge_pairwise(paired)

        assert paired[0].llm_judge_pairwise["final_winner"] == "b"
        assert paired[0].winner_by_metric["llm_judge"] == "b"

    def test_disagreement_is_tie(self):
        b = _make_benchmarker()
        # First pass: A wins. Second pass (swapped): A wins → maps to B. Disagree → tie.
        responses = iter([
            {"winner": "A", "reasoning": "A is great"},
            {"winner": "A", "reasoning": "A is great"},  # in swapped order, this maps to B
        ])
        client = MagicMock()
        client.chat.completions.create.side_effect = lambda **kw: _mock_openai_response(next(responses))

        with patch.object(b, "_get_llm_judge_client", return_value=client):
            paired = self._make_paired(1)
            b.get_llm_judge_pairwise(paired)

        assert paired[0].llm_judge_pairwise["final_winner"] == "tie"

    def test_tie_both_passes(self):
        b = _make_benchmarker()
        responses = iter([
            {"winner": "tie", "reasoning": "equal"},
            {"winner": "tie", "reasoning": "equal"},
        ])
        client = MagicMock()
        client.chat.completions.create.side_effect = lambda **kw: _mock_openai_response(next(responses))

        with patch.object(b, "_get_llm_judge_client", return_value=client):
            paired = self._make_paired(1)
            b.get_llm_judge_pairwise(paired)

        assert paired[0].llm_judge_pairwise["final_winner"] == "tie"
        assert paired[0].winner_by_metric["llm_judge"] == "tie"

    def test_api_error_defaults_to_tie(self):
        b = _make_benchmarker()
        client = MagicMock()
        client.chat.completions.create.side_effect = Exception("fail")

        with patch.object(b, "_get_llm_judge_client", return_value=client):
            paired = self._make_paired(1)
            b.get_llm_judge_pairwise(paired)

        assert paired[0].llm_judge_pairwise["final_winner"] == "tie"
        assert paired[0].llm_judge_pairwise.get("error") is True

    def test_multiple_questions(self):
        b = _make_benchmarker()
        responses = iter([
            {"winner": "A", "reasoning": "1"}, {"winner": "B", "reasoning": "1"},  # Q0 → A
            {"winner": "B", "reasoning": "2"}, {"winner": "A", "reasoning": "2"},  # Q1 → B
            {"winner": "tie", "reasoning": "3"}, {"winner": "tie", "reasoning": "3"},  # Q2 → tie
        ])
        client = MagicMock()
        client.chat.completions.create.side_effect = lambda **kw: _mock_openai_response(next(responses))

        with patch.object(b, "_get_llm_judge_client", return_value=client):
            paired = self._make_paired(3)
            b.get_llm_judge_pairwise(paired)

        assert paired[0].winner_by_metric["llm_judge"] == "a"
        assert paired[1].winner_by_metric["llm_judge"] == "b"
        assert paired[2].winner_by_metric["llm_judge"] == "tie"
        # 2 calls per question (position swap)
        assert client.chat.completions.create.call_count == 6


# ── _get_llm_judge_settings ─────────────────────────────────────────

class TestGetLLMJudgeSettings:
    def test_returns_settings_from_config(self):
        b = _make_benchmarker()
        settings = b._get_llm_judge_settings()
        assert settings["evaluator_model"] == "test-model"
        assert "correctness" in settings["dimensions"]

    def test_returns_empty_when_missing(self):
        b = _make_benchmarker(benchmarking_configs={})
        settings = b._get_llm_judge_settings()
        assert settings == {}

    def test_default_model_fallback(self):
        b = _make_benchmarker(benchmarking_configs={
            "mode_settings": {"llm_judge_settings": {}}
        })
        settings = b._get_llm_judge_settings()
        # get_llm_judge_results uses .get("evaluator_model", "gpt-5-2025-08-07")
        assert settings.get("evaluator_model", "gpt-5-2025-08-07") == "gpt-5-2025-08-07"
