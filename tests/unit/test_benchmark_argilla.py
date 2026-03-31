"""
Unit tests for Argilla integration in benchmark_argilla.py.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest

from src.utils.benchmark_argilla import (
    _collapsible,
    _get_client,
    _get_workspace,
    _format_trace_html,
    _html_escape,
    generate_dataset_name,
    pull_grades_from_argilla,
    pull_multi_grades_from_argilla,
    push_ab_results_to_argilla,
    push_multi_ab_results_to_argilla,
    push_single_results_to_argilla,
    read_state_file,
    read_state_file_full,
    write_state_file,
)


# -- Fixtures -----------------------------------------------------------------

SAMPLE_AB_DATA = {
    "ab_comparison": {
        "config_a": {"model": "gpt-4o"},
        "config_b": {"model": "gpt-3.5-turbo"},
        "per_question": [
            {
                "question": "What is X?",
                "reference_answer": "X is a variable.",
                "answer_a": "X is a letter.",
                "answer_b": "X is a variable.",
                "time_a": 1.2,
                "time_b": 0.8,
                "ragas_a": {"answer_relevancy": 0.85, "faithfulness": 0.9},
                "ragas_b": {"answer_relevancy": 0.95, "faithfulness": 0.88},
                "messages_a": [
                    {"type": "tool_call", "tool_name": "search", "tool_args": {"query": "what is X"}, "tool_output": "Found: X is a letter.", "total_duration": 500000000},
                    {"type": "ai_message", "content": "X is a letter.", "total_duration": 200000000},
                ],
                "messages_b": [
                    {"type": "ai_message", "content": "X is a variable.", "total_duration": 300000000},
                ],
            },
            {
                "question": "What is Y?",
                "reference_answer": "Y is another variable.",
                "answer_a": "Y depends on X.",
                "answer_b": "Y is unknown.",
                "time_a": 2.0,
                "time_b": 1.5,
                "ragas_a": {"answer_relevancy": 0.7},
                "ragas_b": {"answer_relevancy": 0.6},
            },
        ],
    },
}

SAMPLE_SINGLE_DATA = {
    "benchmarking_results": [
        {
            "config": {"model": "gpt-4o"},
            "single_question_results": {
                "question_0": {
                    "question": "What is X?",
                    "reference_answer": "X is a variable.",
                    "answer": "X is a letter.",
                    "answer_relevancy": 0.85,
                    "faithfulness": 0.9,
                    "time_elapsed": 1.2,
                    "messages": [
                        {"type": "tool_call", "tool_name": "search", "tool_args": {"query": "X variable"}, "tool_output": "Doc about X.", "total_duration": 100000000},
                    ],
                },
                "question_1": {
                    "question": "What is Y?",
                    "reference_answer": "Y is another variable.",
                    "answer": "Y depends on X.",
                    "answer_relevancy": 0.7,
                    "faithfulness": None,
                    "time_elapsed": 2.0,
                },
            },
        }
    ],
}


# -- _get_client tests --------------------------------------------------------


@patch.dict(os.environ, {"ARGILLA_API_URL": "http://test:6900", "ARGILLA_API_KEY": "test-key"})
@patch("src.utils.benchmark_argilla.rg", create=True)
def test_get_client_uses_env_vars(mock_rg):
    """Client is initialised with env var values."""
    # We need to patch the import inside the function
    with patch.dict("sys.modules", {"argilla": mock_rg}):
        mock_rg.Argilla.return_value = MagicMock()
        client = _get_client()
        mock_rg.Argilla.assert_called_once_with(api_url="http://test:6900", api_key="test-key")


@patch.dict(os.environ, {}, clear=True)
@patch("src.utils.benchmark_argilla.rg", create=True)
def test_get_client_defaults(mock_rg):
    """Client falls back to default URL and key when env vars not set."""
    # Remove ARGILLA vars if present
    env = {k: v for k, v in os.environ.items() if not k.startswith("ARGILLA")}
    with patch.dict(os.environ, env, clear=True):
        with patch.dict("sys.modules", {"argilla": mock_rg}):
            mock_rg.Argilla.return_value = MagicMock()
            _get_client()
            mock_rg.Argilla.assert_called_once_with(
                api_url="http://localhost:6900", api_key="owner.apikey"
            )


# -- _get_workspace tests -----------------------------------------------------


def test_get_workspace_env_var():
    """Workspace comes from ARGILLA_WORKSPACE env var."""
    with patch.dict(os.environ, {"ARGILLA_WORKSPACE": "my-ws"}):
        assert _get_workspace(None) == "my-ws"


def test_get_workspace_default():
    """Default workspace is 'admin'."""
    env = {k: v for k, v in os.environ.items() if k != "ARGILLA_WORKSPACE"}
    with patch.dict(os.environ, env, clear=True):
        assert _get_workspace(None) == "admin"


# -- generate_dataset_name tests ----------------------------------------------


def test_generate_dataset_name_with_prefix():
    """Generates name with benchmark prefix and timestamp."""
    name = generate_dataset_name("my-bench")
    assert name.startswith("my-bench-")
    # Timestamp portion: YYYYMMDD-HHMMSS
    parts = name.split("-", 2)
    assert len(parts) >= 3


def test_generate_dataset_name_default():
    """Default prefix is archi-bench."""
    name = generate_dataset_name()
    assert name.startswith("archi-bench-")


def test_generate_dataset_name_empty_string():
    """Empty string prefix also uses default."""
    name = generate_dataset_name("")
    assert name.startswith("archi-bench-")


# -- push_ab_results_to_argilla tests ----------------------------------------


@patch("src.utils.benchmark_argilla._get_workspace", return_value="admin")
@patch("src.utils.benchmark_argilla._get_client")
def test_push_ab_results_creates_dataset(mock_client, mock_ws):
    """A/B push creates a dataset and logs records."""
    rg_mock = MagicMock()
    with patch.dict("sys.modules", {"argilla": rg_mock}):
        mock_dataset = MagicMock()
        rg_mock.Dataset.return_value = mock_dataset
        rg_mock.Settings = MagicMock()
        rg_mock.TextField = MagicMock()
        rg_mock.LabelQuestion = MagicMock()
        rg_mock.RatingQuestion = MagicMock()
        rg_mock.TextQuestion = MagicMock()
        rg_mock.FloatMetadataProperty = MagicMock()
        rg_mock.Record = MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw))

        result = push_ab_results_to_argilla(SAMPLE_AB_DATA, "test-dataset")

        assert result == "test-dataset"
        mock_dataset.create.assert_called_once()
        mock_dataset.records.log.assert_called_once()
        logged_records = mock_dataset.records.log.call_args[0][0]
        assert len(logged_records) == 2
        # First record should have trace fields as HTML strings
        assert "trace_a" in logged_records[0].fields
        assert "trace_b" in logged_records[0].fields
        # Trace A should contain the tool call rendered as HTML
        assert "search" in logged_records[0].fields["trace_a"]
        assert "<details>" in logged_records[0].fields["trace_a"]


@patch("src.utils.benchmark_argilla._get_workspace", return_value="admin")
@patch("src.utils.benchmark_argilla._get_client")
def test_push_ab_results_no_ab_comparison_raises(mock_client, mock_ws):
    """Raises ValueError when benchmark data has no ab_comparison."""
    with pytest.raises(ValueError, match="No ab_comparison"):
        push_ab_results_to_argilla({"benchmarking_results": []}, "test-dataset")


@patch("src.utils.benchmark_argilla._get_workspace", return_value="admin")
@patch("src.utils.benchmark_argilla._get_client")
def test_push_ab_results_handles_nan_metadata(mock_client, mock_ws):
    """NaN values in RAGAS scores are excluded from metadata."""
    data = {
        "ab_comparison": {
            "per_question": [
                {
                    "question": "Q1",
                    "reference_answer": "A1",
                    "answer_a": "A",
                    "answer_b": "B",
                    "ragas_a": {"answer_relevancy": float("nan")},
                    "ragas_b": {"faithfulness": 0.5},
                },
            ],
        },
    }

    rg_mock = MagicMock()
    captured_records = []

    def capture_record(**kw):
        captured_records.append(kw)
        return SimpleNamespace(**kw)

    with patch.dict("sys.modules", {"argilla": rg_mock}):
        mock_dataset = MagicMock()
        rg_mock.Dataset.return_value = mock_dataset
        rg_mock.Settings = MagicMock()
        rg_mock.TextField = MagicMock()
        rg_mock.LabelQuestion = MagicMock()
        rg_mock.RatingQuestion = MagicMock()
        rg_mock.TextQuestion = MagicMock()
        rg_mock.FloatMetadataProperty = MagicMock()
        rg_mock.Record = MagicMock(side_effect=capture_record)

        push_ab_results_to_argilla(data, "test-nan")

        assert len(captured_records) == 1
        meta = captured_records[0]["metadata"]
        # NaN should be excluded, valid value included
        assert "ragas_relevancy_a" not in meta
        assert "ragas_faithfulness_b" in meta
        assert meta["ragas_faithfulness_b"] == 0.5


# -- push_single_results_to_argilla tests ------------------------------------


@patch("src.utils.benchmark_argilla._get_workspace", return_value="admin")
@patch("src.utils.benchmark_argilla._get_client")
def test_push_single_results_creates_dataset(mock_client, mock_ws):
    """Single-config push creates a dataset and logs records."""
    rg_mock = MagicMock()
    with patch.dict("sys.modules", {"argilla": rg_mock}):
        mock_dataset = MagicMock()
        rg_mock.Dataset.return_value = mock_dataset
        rg_mock.Settings = MagicMock()
        rg_mock.TextField = MagicMock()
        rg_mock.RatingQuestion = MagicMock()
        rg_mock.TextQuestion = MagicMock()
        rg_mock.FloatMetadataProperty = MagicMock()
        rg_mock.Record = MagicMock(side_effect=lambda **kw: SimpleNamespace(**kw))

        result = push_single_results_to_argilla(SAMPLE_SINGLE_DATA, "test-single")

        assert result == "test-single"
        mock_dataset.create.assert_called_once()
        mock_dataset.records.log.assert_called_once()
        logged_records = mock_dataset.records.log.call_args[0][0]
        assert len(logged_records) == 2
        # First record should have trace as HTML string
        assert "trace" in logged_records[0].fields
        assert "<details>" in logged_records[0].fields["trace"]
        assert "search" in logged_records[0].fields["trace"]


@patch("src.utils.benchmark_argilla._get_workspace", return_value="admin")
@patch("src.utils.benchmark_argilla._get_client")
def test_push_single_results_no_results_raises(mock_client, mock_ws):
    """Raises ValueError when no benchmarking_results present."""
    with pytest.raises(ValueError, match="No benchmarking_results"):
        push_single_results_to_argilla({"benchmarking_results": []}, "test-single")


@patch("src.utils.benchmark_argilla._get_workspace", return_value="admin")
@patch("src.utils.benchmark_argilla._get_client")
def test_push_single_results_none_metadata_excluded(mock_client, mock_ws):
    """None values in RAGAS scores are excluded from metadata."""
    data = {
        "benchmarking_results": [
            {
                "single_question_results": {
                    "q0": {
                        "question": "Q",
                        "reference_answer": "A",
                        "answer": "X",
                        "answer_relevancy": None,
                        "faithfulness": 0.8,
                        "time_elapsed": None,
                    }
                }
            }
        ]
    }

    rg_mock = MagicMock()
    captured_records = []

    def capture_record(**kw):
        captured_records.append(kw)
        return SimpleNamespace(**kw)

    with patch.dict("sys.modules", {"argilla": rg_mock}):
        mock_dataset = MagicMock()
        rg_mock.Dataset.return_value = mock_dataset
        rg_mock.Settings = MagicMock()
        rg_mock.TextField = MagicMock()
        rg_mock.RatingQuestion = MagicMock()
        rg_mock.TextQuestion = MagicMock()
        rg_mock.FloatMetadataProperty = MagicMock()
        rg_mock.Record = MagicMock(side_effect=capture_record)

        push_single_results_to_argilla(data, "test-none")

        meta = captured_records[0]["metadata"]
        assert "ragas_relevancy" not in meta
        assert meta["ragas_faithfulness"] == 0.8
        assert "time_elapsed" not in meta


# -- pull_grades_from_argilla tests -------------------------------------------


@patch("src.utils.benchmark_argilla._get_workspace", return_value="admin")
@patch("src.utils.benchmark_argilla._get_client")
def test_pull_grades_returns_annotated_records(mock_client, mock_ws):
    """Pulls submitted annotations and returns grades dict."""
    mock_ds = MagicMock()
    mock_client.return_value.datasets.return_value = mock_ds

    # Build mock records
    response1 = SimpleNamespace(
        status="submitted",
        user_id="user1",
        values={
            "winner": SimpleNamespace(value="A"),
            "quality": SimpleNamespace(value=4),
            "notes": SimpleNamespace(value="Good answer"),
        },
    )
    record1 = SimpleNamespace(
        fields={"question": "What is X?"},
        responses=[response1],
    )

    response2 = SimpleNamespace(
        status="draft",  # not submitted — should be skipped
        user_id="user2",
        values={"winner": SimpleNamespace(value="B")},
    )
    record2 = SimpleNamespace(
        fields={"question": "What is Y?"},
        responses=[response2],
    )

    mock_ds.records.return_value = [record1, record2]

    grades = pull_grades_from_argilla("test-dataset")

    assert "What is X?" in grades
    assert len(grades["What is X?"]["responses"]) == 1
    assert grades["What is X?"]["responses"][0]["winner"] == "A"
    assert grades["What is X?"]["responses"][0]["quality"] == 4
    assert grades["What is X?"]["responses"][0]["notes"] == "Good answer"

    # Y has only a draft response, so no submitted responses
    assert "What is Y?" in grades
    assert len(grades["What is Y?"]["responses"]) == 0


@patch("src.utils.benchmark_argilla._get_workspace", return_value="admin")
@patch("src.utils.benchmark_argilla._get_client")
def test_pull_grades_dataset_not_found_raises(mock_client, mock_ws):
    """Raises ValueError when dataset doesn't exist."""
    mock_client.return_value.datasets.side_effect = Exception("Not found")

    with pytest.raises(ValueError, match="Could not find"):
        pull_grades_from_argilla("nonexistent")


@patch("src.utils.benchmark_argilla._get_workspace", return_value="admin")
@patch("src.utils.benchmark_argilla._get_client")
def test_pull_grades_dataset_none_raises(mock_client, mock_ws):
    """Raises ValueError when datasets() returns None."""
    mock_client.return_value.datasets.return_value = None

    with pytest.raises(ValueError, match="not found"):
        pull_grades_from_argilla("missing")


@patch("src.utils.benchmark_argilla._get_workspace", return_value="admin")
@patch("src.utils.benchmark_argilla._get_client")
def test_pull_grades_writes_output_file(mock_client, mock_ws, tmp_path):
    """Writes grades to output file when path specified."""
    mock_ds = MagicMock()
    mock_client.return_value.datasets.return_value = mock_ds

    response = SimpleNamespace(
        status="submitted",
        user_id="user1",
        values={"quality": SimpleNamespace(value=3)},
    )
    record = SimpleNamespace(
        fields={"question": "Q1"},
        responses=[response],
    )
    mock_ds.records.return_value = [record]

    output_file = tmp_path / "grades.json"
    grades = pull_grades_from_argilla("test-ds", str(output_file))

    assert output_file.exists()
    loaded = json.loads(output_file.read_text())
    assert "Q1" in loaded
    assert loaded["Q1"]["responses"][0]["quality"] == 3


@patch("src.utils.benchmark_argilla._get_workspace", return_value="admin")
@patch("src.utils.benchmark_argilla._get_client")
def test_pull_grades_multiple_annotators(mock_client, mock_ws):
    """Handles multiple submitted responses from different annotators."""
    mock_ds = MagicMock()
    mock_client.return_value.datasets.return_value = mock_ds

    resp1 = SimpleNamespace(
        status="submitted",
        user_id="user1",
        values={"winner": SimpleNamespace(value="A"), "quality": SimpleNamespace(value=5)},
    )
    resp2 = SimpleNamespace(
        status="submitted",
        user_id="user2",
        values={"winner": SimpleNamespace(value="B"), "quality": SimpleNamespace(value=3)},
    )
    record = SimpleNamespace(
        fields={"question": "Q1"},
        responses=[resp1, resp2],
    )
    mock_ds.records.return_value = [record]

    grades = pull_grades_from_argilla("test-ds")

    assert len(grades["Q1"]["responses"]) == 2
    winners = [r["winner"] for r in grades["Q1"]["responses"]]
    assert set(winners) == {"A", "B"}


# -- State file tests ----------------------------------------------------------


def test_write_and_read_state_file(tmp_path):
    """write_state_file creates file, read_state_file reads it back."""
    with patch.dict(os.environ, {"ARCHI_DIR": str(tmp_path)}):
        write_state_file("my-dataset-123", out_dir="/tmp/results")

        name = read_state_file()
        assert name == "my-dataset-123"

        full = read_state_file_full()
        assert full["dataset_name"] == "my-dataset-123"
        assert full["out_dir"] == "/tmp/results"
        assert "timestamp" in full


def test_write_state_file_merges_existing(tmp_path):
    """Writing state merges with existing data rather than overwriting."""
    with patch.dict(os.environ, {"ARCHI_DIR": str(tmp_path)}):
        # First write with out_dir
        write_state_file("ds-1", out_dir="/first")

        # Second write updates dataset_name but preserves out_dir if not given
        write_state_file("ds-2")

        full = read_state_file_full()
        assert full["dataset_name"] == "ds-2"
        assert full["out_dir"] == "/first"


def test_read_state_file_missing(tmp_path):
    """Returns None when state file doesn't exist."""
    with patch.dict(os.environ, {"ARCHI_DIR": str(tmp_path / "nonexistent")}):
        assert read_state_file() is None
        assert read_state_file_full() is None


def test_read_state_file_corrupt(tmp_path):
    """Returns None for corrupt state file."""
    with patch.dict(os.environ, {"ARCHI_DIR": str(tmp_path)}):
        state_file = tmp_path / ".last-benchmark"
        state_file.write_text("not valid json{{{")

        assert read_state_file() is None
        assert read_state_file_full() is None


# -- _format_trace_html tests ------------------------------------------------


def test_format_trace_html_empty_messages():
    """Empty message list returns collapsible with placeholder text."""
    result = _format_trace_html([])
    assert "No trace data available" in result
    assert "<details>" in result


def test_format_trace_html_tool_call():
    """Tool call messages render with green border, collapsible input."""
    messages = [
        {"type": "tool_call", "tool_name": "search_docs", "tool_args": {"query": "what is X"}, "total_duration": 500000000},
    ]
    html = _format_trace_html(messages)
    assert "#4CAF50" in html
    assert "search_docs" in html
    assert "what is X" in html
    assert "0.50s" in html
    assert "Input" in html
    # Args are in a nested collapsible
    assert html.count("<details>") >= 2


def test_format_trace_html_ai_message():
    """AI messages render with blue border."""
    messages = [
        {"type": "ai_message", "content": "The answer is 42.", "total_duration": 1000000000},
    ]
    html = _format_trace_html(messages)
    assert "#2196F3" in html
    assert "The answer is 42." in html
    assert "1.00s" in html
    assert "<details>" in html


def test_format_trace_html_truncates_long_content():
    """Long AI message content is truncated at 500 chars."""
    long_content = "x" * 600
    messages = [{"type": "ai_message", "content": long_content}]
    html = _format_trace_html(messages)
    assert "\u2026" in html
    assert "x" * 500 in html
    assert "x" * 501 not in html


def test_format_trace_html_multiple_steps():
    """Multiple steps render in order."""
    messages = [
        {"type": "tool_call", "tool_name": "search", "tool_args": {"query": "test query"}},
        {"type": "ai_message", "content": "result"},
    ]
    html = _format_trace_html(messages)
    assert html.index("search") < html.index("result")


def test_format_trace_html_tool_output():
    """Tool output renders as a collapsible block."""
    messages = [
        {"type": "tool_call", "tool_name": "search", "tool_args": {"query": "q"}, "tool_output": "Found 3 results."},
    ]
    html = _format_trace_html(messages)
    assert "Output" in html
    assert "Found 3 results" in html


def test_format_trace_html_thinking():
    """Thinking content renders as a purple-bordered collapsible step."""
    messages = [
        {"type": "ai_message", "content": "The answer.", "thinking": "Let me reason about this..."},
    ]
    html = _format_trace_html(messages)
    assert "#9C27B0" in html
    assert "Thinking" in html
    assert "Show reasoning" in html
    assert "Let me reason about this" in html
    # AI message still renders
    assert "The answer." in html


def test_format_trace_html_dict_args():
    """Dict tool_args are JSON-formatted in the collapsible."""
    messages = [
        {"type": "tool_call", "tool_name": "search", "tool_args": {"query": "test", "limit": 5}},
    ]
    html = _format_trace_html(messages)
    assert "&quot;query&quot;" in html
    assert "&quot;test&quot;" in html


def test_format_trace_html_no_duration():
    """Messages without duration omit the duration span element."""
    messages = [{"type": "ai_message", "content": "hello"}]
    html = _format_trace_html(messages)
    assert "float:right" not in html


# -- _html_escape tests ------------------------------------------------------


def test_format_trace_html_custom_label():
    """Custom label appears in the summary element."""
    messages = [{"type": "ai_message", "content": "hello"}]
    html = _format_trace_html(messages, label="Custom label")
    assert "Custom label" in html
    assert "<details>" in html


def test_html_escape_special_chars():
    """HTML special characters are escaped."""
    assert _html_escape('<script>alert("xss")</script>') == '&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;'
    assert _html_escape("a & b") == "a &amp; b"


# -- generate_dataset_name with suffix tests ----------------------------------

def test_generate_dataset_name_with_suffix():
    """Suffix is included in the generated name."""
    name = generate_dataset_name("bench", suffix="alpha-vs-beta")
    assert "bench-alpha-vs-beta-" in name


# -- push_multi_ab_results_to_argilla tests -----------------------------------

@patch("src.utils.benchmark_argilla.push_ab_results_to_argilla")
def test_push_multi_ab_creates_datasets(mock_push):
    """Creates one dataset per comparison pair."""
    mock_push.side_effect = lambda data, name: name

    comparisons = [
        {
            "config_a": {"name": "alpha"},
            "config_b": {"name": "beta"},
            "per_question": [{"question": "Q1", "answer_a": "A1", "answer_b": "B1"}],
        },
        {
            "config_a": {"name": "alpha"},
            "config_b": {"name": "gamma"},
            "per_question": [{"question": "Q1", "answer_a": "A1", "answer_b": "C1"}],
        },
    ]

    result = push_multi_ab_results_to_argilla(comparisons, "test-bench")
    assert len(result) == 2
    assert "alpha-vs-beta" in result[0]
    assert "alpha-vs-gamma" in result[1]
    assert mock_push.call_count == 2


@patch("src.utils.benchmark_argilla.push_ab_results_to_argilla")
def test_push_multi_ab_handles_errors(mock_push):
    """Continues pushing remaining pairs when one fails."""
    mock_push.side_effect = [Exception("push failed"), "success-name"]

    comparisons = [
        {"config_a": {"name": "a"}, "config_b": {"name": "b"}, "per_question": []},
        {"config_a": {"name": "c"}, "config_b": {"name": "d"}, "per_question": []},
    ]

    result = push_multi_ab_results_to_argilla(comparisons, "bench")
    assert len(result) == 1
    assert "c-vs-d" in result[0]


@patch("src.utils.benchmark_argilla.push_ab_results_to_argilla")
def test_push_multi_ab_empty_list(mock_push):
    """Empty comparisons list returns empty dataset names."""
    result = push_multi_ab_results_to_argilla([], "bench")
    assert result == []
    mock_push.assert_not_called()


@patch("src.utils.benchmark_argilla.push_ab_results_to_argilla")
def test_push_multi_ab_default_names(mock_push):
    """Falls back to config_a/config_b when names are missing."""
    mock_push.side_effect = lambda data, name: name

    comparisons = [
        {"config_a": {}, "config_b": {}, "per_question": []},
    ]
    result = push_multi_ab_results_to_argilla(comparisons, "bench")
    assert len(result) == 1
    assert "config_a-vs-config_b" in result[0]


# -- pull_multi_grades_from_argilla tests -------------------------------------

@patch("src.utils.benchmark_argilla.pull_grades_from_argilla")
def test_pull_multi_grades_merges_datasets(mock_pull):
    """Merges grades from multiple datasets."""
    mock_pull.side_effect = [
        {"Q1": {"question": "Q1", "responses": [{"winner": "A"}]}},
        {"Q2": {"question": "Q2", "responses": []}},
    ]

    result = pull_multi_grades_from_argilla(["ds-1", "ds-2"])
    assert len(result["datasets"]) == 2
    assert result["summary"]["total_annotated"] == 1
    assert result["summary"]["total_questions"] == 2


@patch("src.utils.benchmark_argilla.pull_grades_from_argilla")
def test_pull_multi_grades_handles_errors(mock_pull):
    """Records errors for failed datasets."""
    mock_pull.side_effect = Exception("not found")

    result = pull_multi_grades_from_argilla(["ds-fail"])
    assert "error" in result["datasets"]["ds-fail"]


@patch("src.utils.benchmark_argilla.pull_grades_from_argilla")
def test_pull_multi_grades_writes_output(mock_pull, tmp_path):
    """Writes merged grades to output file."""
    mock_pull.return_value = {"Q1": {"question": "Q1", "responses": []}}

    out = str(tmp_path / "multi_grades.json")
    pull_multi_grades_from_argilla(["ds-1"], output_path=out)

    assert Path(out).exists()
    loaded = json.loads(Path(out).read_text())
    assert "datasets" in loaded


# -- write_state_file with dataset_names tests --------------------------------

def test_write_state_file_with_dataset_names(tmp_path):
    """State file stores multiple dataset names."""
    with patch.dict(os.environ, {"ARCHI_DIR": str(tmp_path)}):
        write_state_file("ds-1", dataset_names=["ds-1", "ds-2", "ds-3"])
        full = read_state_file_full()
        assert full["dataset_name"] == "ds-1"
        assert full["dataset_names"] == ["ds-1", "ds-2", "ds-3"]


def test_write_state_file_dataset_names_preserved_on_merge(tmp_path):
    """dataset_names persists across writes when not overwritten."""
    with patch.dict(os.environ, {"ARCHI_DIR": str(tmp_path)}):
        write_state_file("ds-1", dataset_names=["ds-1", "ds-2"])
        write_state_file("ds-1")  # No dataset_names
        full = read_state_file_full()
        # dataset_names should be preserved from previous write
        assert full.get("dataset_names") == ["ds-1", "ds-2"]
