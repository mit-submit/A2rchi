"""
Argilla integration for Archi benchmark results.

Pushes benchmark results to Argilla for team-based human grading,
and exports submitted annotations back to local JSON.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.logging import get_logger

logger = get_logger(__name__)

RAGAS_METRICS = ["answer_relevancy", "faithfulness", "context_precision", "context_recall"]

# Inline style fragments applied directly to elements because Argilla's
# markdown renderer strips <style> tags.
_STEP_BASE = "margin:4px 0;padding:6px 10px;border-left:3px solid #ccc;font-size:.92em;line-height:1.4"
_STEP_TOOL = "margin:4px 0;padding:6px 10px;border-left:3px solid #4CAF50;font-size:.92em;line-height:1.4"
_STEP_AI = "margin:4px 0;padding:6px 10px;border-left:3px solid #2196F3;font-size:.92em;line-height:1.4"
_STEP_THINK = "margin:4px 0;padding:6px 10px;border-left:3px solid #9C27B0;font-size:.92em;line-height:1.4"
_LABEL_STYLE = "font-weight:600;font-size:.85em;text-transform:uppercase;color:#888"
_DUR_STYLE = "float:right;font-size:.82em;color:#999"
_SUMMARY_STYLE = "cursor:pointer;font-weight:600;padding:4px 0"
_DETAIL_INNER = "cursor:pointer;font-size:.85em;color:#666;margin-top:4px"
_DETAIL_CONTENT = (
    "margin:4px 0 0 0;padding:4px 8px;background:#f8f8f8;border-radius:3px;"
    "font-size:.85em;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto"
)


def _collapsible(summary_text: str, content: str) -> str:
    """Wrap content in a nested collapsible <details> block."""
    return (
        f'<details><summary style="{_DETAIL_INNER}">{_html_escape(summary_text)}</summary>'
        f'<div style="{_DETAIL_CONTENT}">{content}</div></details>'
    )


def _format_trace_html(messages: List[Dict[str, Any]], label: str = "Show agent steps") -> str:
    """Render prepared messages as a collapsible HTML trace block.

    Uses <details>/<summary> so the trace is collapsed by default
    inside a markdown-enabled TextField.  All styling is inline because
    Argilla strips <style> tags.
    """
    if not messages:
        return (
            f'<details><summary style="{_SUMMARY_STYLE}">\u25b6 {_html_escape(label)}</summary>'
            '<p style="color:#999;font-style:italic;">No trace data available.</p>'
            '</details>'
        )

    steps: List[str] = []
    for msg in messages:
        msg_type = msg.get("type", "unknown")
        duration = msg.get("total_duration")
        dur_str = ""
        if duration is not None:
            secs = duration / 1e9 if duration > 1e6 else duration
            dur_str = f'<span style="{_DUR_STYLE}">{secs:.2f}s</span>'

        if msg_type == "tool_call":
            name = _html_escape(str(msg.get("tool_name", "unknown")))
            # Build sub-details for args and output
            raw_args = msg.get("tool_args", "")
            if isinstance(raw_args, dict):
                args_text = _html_escape(json.dumps(raw_args, indent=2, default=str))
            else:
                args_text = _html_escape(str(raw_args))
            nested = _collapsible("\u25b8 Input", args_text)
            raw_output = msg.get("tool_output")
            if raw_output is not None:
                output_text = _html_escape(str(raw_output))
                if len(output_text) > 2000:
                    output_text = output_text[:2000] + "\u2026"
                nested += _collapsible("\u25b8 Output", output_text)
            steps.append(
                f'<div style="{_STEP_TOOL}">'
                f'{dur_str}<span style="{_LABEL_STYLE}">Tool Call</span><br>'
                f'<strong>{name}</strong>{nested}</div>'
            )
        elif msg_type == "ai_message":
            content = _html_escape(str(msg.get("content", "")))
            if len(content) > 500:
                content = content[:500] + "\u2026"
            # Thinking step (shown before the message content)
            thinking = msg.get("thinking", "")
            think_html = ""
            if thinking:
                think_text = _html_escape(str(thinking))
                if len(think_text) > 2000:
                    think_text = think_text[:2000] + "\u2026"
                steps.append(
                    f'<div style="{_STEP_THINK}">'
                    f'<span style="{_LABEL_STYLE}">Thinking</span>'
                    f'{_collapsible(chr(0x25b8) + " Show reasoning", think_text)}</div>'
                )
            steps.append(
                f'<div style="{_STEP_AI}">'
                f'{dur_str}<span style="{_LABEL_STYLE}">AI Message</span><br>'
                f'{content}</div>'
            )
        else:
            content = _html_escape(str(msg.get("content", msg)))
            steps.append(f'<div style="{_STEP_BASE}">{content}</div>')

    inner = "\n".join(steps)
    return (
        f'<details><summary style="{_SUMMARY_STYLE}">\u25b6 {_html_escape(label)}</summary>'
        f'{inner}'
        f'</details>'
    )


def _html_escape(text: str) -> str:
    """Minimal HTML escaping for untrusted content."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _get_client():
    """Initialize and return an Argilla client."""
    try:
        import argilla as rg
    except ImportError:
        raise ImportError(
            "The 'argilla' package is required for Argilla export. "
            "Install it with: pip install 'argilla>=2.5,<3'"
        )

    api_url = os.environ.get("ARGILLA_API_URL", "http://localhost:6900")
    api_key = os.environ.get("ARGILLA_API_KEY", "owner.apikey")

    return rg.Argilla(api_url=api_url, api_key=api_key)


def _get_workspace(client) -> str:
    """Return the configured Argilla workspace name."""
    return os.environ.get("ARGILLA_WORKSPACE", "admin")


def push_ab_results_to_argilla(
    benchmark_data: Dict[str, Any],
    dataset_name: str,
) -> str:
    """Push A/B benchmark results to Argilla as an annotation dataset.

    Creates an Argilla dataset with one record per question, showing
    answer_a and answer_b side-by-side with RAGAS scores as metadata.

    Returns the dataset name.
    """
    import argilla as rg

    ab = benchmark_data.get("ab_comparison")
    if not ab:
        raise ValueError("No ab_comparison section found in benchmark data.")

    client = _get_client()
    workspace = _get_workspace(client)

    settings = rg.Settings(
        fields=[
            rg.TextField(name="question", title="Question"),
            rg.TextField(name="reference_answer", title="Reference Answer"),
            rg.TextField(name="answer_a", title="Response A", use_markdown=True),
            rg.TextField(name="answer_b", title="Response B", use_markdown=True),
            rg.TextField(
                name="trace_a",
                title="Trace A",
                use_markdown=True,
                required=False,
            ),
            rg.TextField(
                name="trace_b",
                title="Trace B",
                use_markdown=True,
                required=False,
            ),
        ],
        questions=[
            rg.LabelQuestion(
                name="winner",
                title="Which response is better?",
                labels=["A", "B", "Tie"],
                required=True,
            ),
            rg.RatingQuestion(
                name="quality",
                title="Quality of the winning response (1=poor, 5=excellent)",
                values=[1, 2, 3, 4, 5],
                required=True,
            ),
            rg.TextQuestion(
                name="notes",
                title="Notes (optional)",
                required=False,
            ),
        ],
        metadata=[
            rg.FloatMetadataProperty(name="ragas_relevancy_a", title="RAGAS Relevancy (A)"),
            rg.FloatMetadataProperty(name="ragas_relevancy_b", title="RAGAS Relevancy (B)"),
            rg.FloatMetadataProperty(name="ragas_faithfulness_a", title="RAGAS Faithfulness (A)"),
            rg.FloatMetadataProperty(name="ragas_faithfulness_b", title="RAGAS Faithfulness (B)"),
            rg.FloatMetadataProperty(name="ragas_precision_a", title="RAGAS Context Precision (A)"),
            rg.FloatMetadataProperty(name="ragas_precision_b", title="RAGAS Context Precision (B)"),
            rg.FloatMetadataProperty(name="ragas_recall_a", title="RAGAS Context Recall (A)"),
            rg.FloatMetadataProperty(name="ragas_recall_b", title="RAGAS Context Recall (B)"),
            rg.FloatMetadataProperty(name="time_a", title="Response Time (A)"),
            rg.FloatMetadataProperty(name="time_b", title="Response Time (B)"),
        ],
    )

    dataset = rg.Dataset(name=dataset_name, workspace=workspace, settings=settings)
    dataset.create()
    logger.info("Created Argilla dataset: %s", dataset_name)

    per_question = ab.get("per_question", [])
    records = []
    for i, item in enumerate(per_question):
        ragas_a = item.get("ragas_a", {})
        ragas_b = item.get("ragas_b", {})

        metadata = {}
        ar = ragas_a.get("answer_relevancy")
        if ar is not None and ar == ar:
            metadata["ragas_relevancy_a"] = float(ar)
        br = ragas_b.get("answer_relevancy")
        if br is not None and br == br:
            metadata["ragas_relevancy_b"] = float(br)
        af = ragas_a.get("faithfulness")
        if af is not None and af == af:
            metadata["ragas_faithfulness_a"] = float(af)
        bf = ragas_b.get("faithfulness")
        if bf is not None and bf == bf:
            metadata["ragas_faithfulness_b"] = float(bf)
        ap = ragas_a.get("context_precision")
        if ap is not None and ap == ap:
            metadata["ragas_precision_a"] = float(ap)
        bp = ragas_b.get("context_precision")
        if bp is not None and bp == bp:
            metadata["ragas_precision_b"] = float(bp)
        arc = ragas_a.get("context_recall")
        if arc is not None and arc == arc:
            metadata["ragas_recall_a"] = float(arc)
        brc = ragas_b.get("context_recall")
        if brc is not None and brc == brc:
            metadata["ragas_recall_b"] = float(brc)
        ta = item.get("time_a")
        if ta is not None:
            metadata["time_a"] = float(ta)
        tb = item.get("time_b")
        if tb is not None:
            metadata["time_b"] = float(tb)

        # Build collapsible trace HTML from prepared messages
        trace_a_html = _format_trace_html(item.get("messages_a", []))
        trace_b_html = _format_trace_html(item.get("messages_b", []))

        records.append(
            rg.Record(
                fields={
                    "question": item.get("question", f"Question {i+1}"),
                    "reference_answer": item.get("reference_answer", "N/A"),
                    "answer_a": item.get("answer_a", "(no answer)"),
                    "answer_b": item.get("answer_b", "(no answer)"),
                    "trace_a": trace_a_html,
                    "trace_b": trace_b_html,
                },
                metadata=metadata,
            )
        )

    dataset.records.log(records)
    logger.info("Logged %d records to Argilla dataset '%s'.", len(records), dataset_name)
    return dataset_name


def push_single_results_to_argilla(
    benchmark_data: Dict[str, Any],
    dataset_name: str,
) -> str:
    """Push single-config benchmark results to Argilla.

    Creates a dataset with question, response, reference answer,
    and RAGAS scores. No winner label — just quality rating and notes.

    Returns the dataset name.
    """
    import argilla as rg

    results_list = benchmark_data.get("benchmarking_results", [])
    if not results_list:
        raise ValueError("No benchmarking_results found in benchmark data.")

    config_results = results_list[0]
    questions = config_results.get("single_question_results", {})

    client = _get_client()
    workspace = _get_workspace(client)

    settings = rg.Settings(
        fields=[
            rg.TextField(name="question", title="Question"),
            rg.TextField(name="reference_answer", title="Reference Answer"),
            rg.TextField(name="response", title="Response", use_markdown=True),
            rg.TextField(
                name="trace",
                title="Agent Trace",
                use_markdown=True,
                required=False,
            ),
        ],
        questions=[
            rg.RatingQuestion(
                name="quality",
                title="Quality of the response (1=poor, 5=excellent)",
                values=[1, 2, 3, 4, 5],
                required=True,
            ),
            rg.TextQuestion(
                name="notes",
                title="Notes (optional)",
                required=False,
            ),
        ],
        metadata=[
            rg.FloatMetadataProperty(name="ragas_relevancy", title="RAGAS Relevancy"),
            rg.FloatMetadataProperty(name="ragas_faithfulness", title="RAGAS Faithfulness"),
            rg.FloatMetadataProperty(name="ragas_precision", title="RAGAS Context Precision"),
            rg.FloatMetadataProperty(name="ragas_recall", title="RAGAS Context Recall"),
            rg.FloatMetadataProperty(name="time_elapsed", title="Response Time"),
        ],
    )

    dataset = rg.Dataset(name=dataset_name, workspace=workspace, settings=settings)
    dataset.create()
    logger.info("Created Argilla dataset: %s", dataset_name)

    records = []
    for i, (q_key, item) in enumerate(sorted(questions.items())):
        metadata = {}
        ar = item.get("answer_relevancy")
        if ar is not None and ar == ar:
            metadata["ragas_relevancy"] = float(ar)
        af = item.get("faithfulness")
        if af is not None and af == af:
            metadata["ragas_faithfulness"] = float(af)
        cp = item.get("context_precision")
        if cp is not None and cp == cp:
            metadata["ragas_precision"] = float(cp)
        cr = item.get("context_recall")
        if cr is not None and cr == cr:
            metadata["ragas_recall"] = float(cr)
        te = item.get("time_elapsed")
        if te is not None:
            metadata["time_elapsed"] = float(te)

        # Build collapsible trace HTML from prepared messages
        trace_html = _format_trace_html(item.get("messages", []))

        records.append(
            rg.Record(
                fields={
                    "question": item.get("question", q_key),
                    "reference_answer": item.get("reference_answer", "N/A"),
                    "response": item.get("answer", "(no answer)"),
                    "trace": trace_html,
                },
                metadata=metadata,
            )
        )

    dataset.records.log(records)
    logger.info("Logged %d records to Argilla dataset '%s'.", len(records), dataset_name)
    return dataset_name


def pull_grades_from_argilla(
    dataset_name: str,
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Pull submitted annotations from Argilla and return as grades dict.

    Returns a dict keyed by question text. Each entry contains the question,
    scores from all annotators, and notes.
    """
    client = _get_client()
    workspace = _get_workspace(client)

    try:
        dataset = client.datasets(name=dataset_name, workspace=workspace)
    except Exception as e:
        raise ValueError(f"Could not find Argilla dataset '{dataset_name}': {e}")

    if dataset is None:
        raise ValueError(f"Argilla dataset '{dataset_name}' not found in workspace '{workspace}'.")

    grades: Dict[str, Any] = {}
    for record in dataset.records(with_responses=True):
        fields = record.fields
        question = fields.get("question", "unknown")

        item_grades: Dict[str, Any] = {
            "question": question,
            "responses": [],
        }

        for response in record.responses:
            if response.status != "submitted":
                continue
            resp_data: Dict[str, Any] = {
                "user": getattr(response, "user_id", None),
            }
            values = response.values
            if values:
                winner = values.get("winner")
                if winner is not None:
                    resp_data["winner"] = winner.value if hasattr(winner, "value") else winner
                quality = values.get("quality")
                if quality is not None:
                    resp_data["quality"] = quality.value if hasattr(quality, "value") else quality
                notes = values.get("notes")
                if notes is not None:
                    resp_data["notes"] = notes.value if hasattr(notes, "value") else notes
            item_grades["responses"].append(resp_data)

        grades[question] = item_grades

    annotated = sum(1 for g in grades.values() if g.get("responses"))
    logger.info(
        "Pulled grades for dataset '%s': %d/%d questions annotated.",
        dataset_name, annotated, len(grades),
    )

    if annotated == 0:
        logger.warning("No annotations found in dataset '%s'. Grade in Argilla first.", dataset_name)

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(grades, indent=2, default=str))
        logger.info("Grades written to %s", output_path)

    return grades


def generate_dataset_name(benchmark_name: str = "", suffix: str = "") -> str:
    """Generate a timestamped Argilla dataset name."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    prefix = benchmark_name or "archi-bench"
    if suffix:
        return f"{prefix}-{suffix}-{timestamp}"
    return f"{prefix}-{timestamp}"


def push_multi_ab_results_to_argilla(
    ab_comparisons: List[Dict[str, Any]],
    benchmark_name: str,
) -> List[str]:
    """Push multiple pairwise A/B comparisons to Argilla as separate datasets.

    Creates one dataset per pair, named {benchmark_name}-{configA}-vs-{configB}-{timestamp}.

    Returns list of dataset names created.
    """
    dataset_names = []
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    for comp in ab_comparisons:
        name_a = comp.get("config_a", {}).get("name", "config_a")
        name_b = comp.get("config_b", {}).get("name", "config_b")
        dataset_name = f"{benchmark_name}-{name_a}-vs-{name_b}-{timestamp}"

        # Wrap into the format push_ab_results_to_argilla expects
        benchmark_data = {"ab_comparison": comp}
        try:
            push_ab_results_to_argilla(benchmark_data, dataset_name)
            dataset_names.append(dataset_name)
        except Exception:
            logger.exception("Failed to push pair %s vs %s to Argilla.", name_a, name_b)

    logger.info("Created %d Argilla datasets for %d pairs.", len(dataset_names), len(ab_comparisons))
    return dataset_names


def pull_multi_grades_from_argilla(
    dataset_names: List[str],
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Pull grades from multiple Argilla datasets and merge results.

    Returns a dict with:
      - datasets: {dataset_name: grades_dict}
      - summary: {total_annotated, total_questions}
    """
    all_grades: Dict[str, Any] = {"datasets": {}, "summary": {}}
    total_annotated = 0
    total_questions = 0

    for ds_name in dataset_names:
        try:
            grades = pull_grades_from_argilla(ds_name)
            all_grades["datasets"][ds_name] = grades
            annotated = sum(1 for g in grades.values() if g.get("responses"))
            total_annotated += annotated
            total_questions += len(grades)
        except Exception:
            logger.exception("Failed to pull grades from dataset '%s'.", ds_name)
            all_grades["datasets"][ds_name] = {"error": "pull failed"}

    all_grades["summary"] = {
        "total_annotated": total_annotated,
        "total_questions": total_questions,
    }

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(all_grades, indent=2, default=str))
        logger.info("Multi-dataset grades written to %s", output_path)

    return all_grades


# -- State file utilities (shared with benchmark_grading.py) --

def write_state_file(
    dataset_name: str,
    out_dir: Optional[str] = None,
    dataset_names: Optional[List[str]] = None,
):
    """Write the last benchmark state to ~/.archi/.last-benchmark.

    Merges with any existing state so that host-side writes (out_dir) and
    container-side writes (dataset_name) accumulate.
    """
    archi_dir = Path(os.environ.get("ARCHI_DIR", Path.home() / ".archi"))
    archi_dir.mkdir(parents=True, exist_ok=True)
    state_file = archi_dir / ".last-benchmark"

    existing: Dict[str, Any] = {}
    if state_file.exists():
        try:
            existing = json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    existing["dataset_name"] = dataset_name
    existing["timestamp"] = datetime.now(timezone.utc).isoformat()
    if out_dir is not None:
        existing["out_dir"] = out_dir
    if dataset_names is not None:
        existing["dataset_names"] = dataset_names

    state_file.write_text(json.dumps(existing, indent=2))
    logger.info("Wrote last benchmark state to %s", state_file)


def read_state_file() -> Optional[str]:
    """Read the last benchmark dataset name from ~/.archi/.last-benchmark."""
    state = read_state_file_full()
    return state.get("dataset_name") if state else None


def read_state_file_full() -> Optional[Dict[str, Any]]:
    """Read the full last benchmark state from ~/.archi/.last-benchmark."""
    archi_dir = Path(os.environ.get("ARCHI_DIR", Path.home() / ".archi"))
    state_file = archi_dir / ".last-benchmark"
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError):
        return None
