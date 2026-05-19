"""Unit tests for the BareLLMPipeline."""

from unittest.mock import MagicMock

from src.archi.pipelines.classic_pipelines.bare_llm import BareLLMPipeline
from src.archi.utils.output_dataclass import PipelineOutput


def _make_pipeline(model_name="test-model"):
    """Create a BareLLMPipeline without calling __init__, with a mock LLM."""
    p = BareLLMPipeline.__new__(BareLLMPipeline)
    mock_llm = MagicMock()
    mock_llm.model_name = model_name
    mock_response = MagicMock()
    mock_response.content = "This is the LLM response."
    mock_llm.invoke.return_value = mock_response
    p.chat_model = mock_llm
    p.llms = {"chat_model": mock_llm}
    return p, mock_llm


class TestBareLLMInvoke:
    def test_returns_pipeline_output(self):
        p, _ = _make_pipeline()
        result = p.invoke(history=[("User", "What is CMS?")])
        assert isinstance(result, PipelineOutput)
        assert result.answer == "This is the LLM response."

    def test_passes_question_to_llm(self):
        p, mock_llm = _make_pipeline()
        p.invoke(history=[("User", "How does Rucio work?")])
        messages = mock_llm.invoke.call_args[0][0]
        assert len(messages) == 1
        assert messages[0].content == "How does Rucio work?"

    def test_multi_turn_sends_full_history(self):
        p, mock_llm = _make_pipeline()
        history = [
            ("User", "What is JIRA?"),
            ("Assistant", "JIRA is a ticket tracker."),
            ("User", "How do I create a ticket?"),
        ]
        p.invoke(history=history)
        messages = mock_llm.invoke.call_args[0][0]
        assert len(messages) == 3

    def test_metadata_includes_pipeline_name(self):
        p, _ = _make_pipeline(model_name="qwen3:32b")
        result = p.invoke(history=[("User", "test")])
        assert result.metadata["pipeline_used"] == "BareLLMPipeline"
        assert result.metadata["model_used"] == "qwen3:32b"

    def test_no_source_documents(self):
        p, _ = _make_pipeline()
        result = p.invoke(history=[("User", "test")])
        assert result.source_documents == []

    def test_empty_history(self):
        p, mock_llm = _make_pipeline()
        result = p.invoke(history=[])
        assert isinstance(result, PipelineOutput)
        assert result.metadata["question"] == ""
