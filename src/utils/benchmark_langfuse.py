"""
Langfuse exporter for Archi benchmark results.

Uploads benchmark results as Langfuse Datasets + Experiment Runs for
side-by-side comparison and human annotation in the Langfuse UI.

Pre-computed results are uploaded—agents are NOT re-executed through Langfuse.
"""

import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from src.utils.logging import get_logger

logger = get_logger(__name__)

try:
    from langfuse import Evaluation, get_client
except ImportError:
    raise ImportError(
        "The 'langfuse' package is required for Langfuse export. "
        "Install it with: pip install langfuse"
    )

RAGAS_METRICS = ["answer_relevancy", "faithfulness", "context_precision", "context_recall"]


def _init_langfuse():
    """Initialize and verify the Langfuse client."""
    langfuse = get_client()
    if not langfuse.auth_check():
        raise RuntimeError(
            "Langfuse authentication failed. Check LANGFUSE_SECRET_KEY, "
            "LANGFUSE_PUBLIC_KEY, and LANGFUSE_BASE_URL environment variables."
        )
    return langfuse


def _build_dataset_items(paired, queries_to_answers=None):
    """Build dataset item dicts from paired ABResults."""
    items = []
    for i, r in enumerate(paired):
        items.append({
            "input": {"question": r.question},
            "expected_output": {
                "answer": r.reference_answer,
            },
            "metadata": {"question_index": i},
        })
    return items


def _ragas_evaluators():
    """Create evaluator functions that replay pre-computed RAGAS scores."""
    evaluators = []
    for metric in RAGAS_METRICS:
        def make_eval(metric_name):
            def evaluator(*, input, output, expected_output, metadata, **kwargs):
                score = metadata.get(metric_name)
                if score is None or score != score:  # NaN check
                    return None
                return Evaluation(
                    name=metric_name,
                    value=score,
                    comment=f"Pre-computed RAGAS {metric_name} score",
                )
            return evaluator
        evaluators.append(make_eval(metric))
    return evaluators


def export_ab_to_langfuse(
    paired: List,
    ab_comparison: Dict[str, Any],
    benchmark_name: str,
):
    """
    Export A/B benchmark results to Langfuse.

    Creates a Dataset with one item per question, then runs two Experiments
    (one per config) replaying pre-computed answers and RAGAS scores.
    """
    langfuse = _init_langfuse()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    config_a = ab_comparison.get("config_a", {})
    config_b = ab_comparison.get("config_b", {})

    dataset_name = f"archi-ab/{benchmark_name}-{timestamp}"
    logger.info("Creating Langfuse dataset: %s", dataset_name)

    langfuse.create_dataset(
        name=dataset_name,
        description=f"A/B benchmark: {config_a.get('agent_class', '?')} vs {config_b.get('agent_class', '?')}",
        metadata={
            "config_a": config_a,
            "config_b": config_b,
            "benchmark_name": benchmark_name,
            "created_at": timestamp,
        },
    )

    # Upload dataset items
    for i, r in enumerate(paired):
        langfuse.create_dataset_item(
            dataset_name=dataset_name,
            input={"question": r.question},
            expected_output={"answer": r.reference_answer},
            metadata={"question_index": i},
        )

    dataset = langfuse.get_dataset(dataset_name)

    # --- Experiment A ---
    label_a = f"{config_a.get('agent_class', 'A')}/{config_a.get('provider', '?')}/{config_a.get('model', '?')}"
    logger.info("Running Langfuse experiment A: %s", label_a)

    answers_a = [r.answer_a for r in paired]
    ragas_a = [r.ragas_a for r in paired]

    def task_a(*, item, **kwargs):
        idx = item.metadata.get("question_index", 0) if hasattr(item, "metadata") else item.get("metadata", {}).get("question_index", 0)
        return answers_a[idx]

    def make_ragas_evaluator_a(metric_name):
        def evaluator(*, input, output, expected_output, metadata, **kwargs):
            idx = metadata.get("question_index", 0)
            score = ragas_a[idx].get(metric_name)
            if score is None or score != score:
                return None
            return Evaluation(name=metric_name, value=score)
        return evaluator

    evaluators_a = [make_ragas_evaluator_a(m) for m in RAGAS_METRICS]

    result_a = dataset.run_experiment(
        name=f"Config A: {label_a}",
        description=f"Benchmark config A — {config_a.get('config_file', '')}",
        task=task_a,
        evaluators=evaluators_a,
        metadata={"config": config_a, "arm": "a"},
    )

    # --- Experiment B ---
    label_b = f"{config_b.get('agent_class', 'B')}/{config_b.get('provider', '?')}/{config_b.get('model', '?')}"
    logger.info("Running Langfuse experiment B: %s", label_b)

    answers_b = [r.answer_b for r in paired]
    ragas_b = [r.ragas_b for r in paired]

    def task_b(*, item, **kwargs):
        idx = item.metadata.get("question_index", 0) if hasattr(item, "metadata") else item.get("metadata", {}).get("question_index", 0)
        return answers_b[idx]

    def make_ragas_evaluator_b(metric_name):
        def evaluator(*, input, output, expected_output, metadata, **kwargs):
            idx = metadata.get("question_index", 0)
            score = ragas_b[idx].get(metric_name)
            if score is None or score != score:
                return None
            return Evaluation(name=metric_name, value=score)
        return evaluator

    evaluators_b = [make_ragas_evaluator_b(m) for m in RAGAS_METRICS]

    result_b = dataset.run_experiment(
        name=f"Config B: {label_b}",
        description=f"Benchmark config B — {config_b.get('config_file', '')}",
        task=task_b,
        evaluators=evaluators_b,
        metadata={"config": config_b, "arm": "b"},
    )

    langfuse.flush()
    logger.info(
        "Langfuse A/B export complete. Dataset: '%s'. "
        "Open your Langfuse UI to compare experiments side-by-side.",
        dataset_name,
    )


def export_single_to_langfuse(
    results: List[Dict[str, Any]],
    metadata: Dict[str, Any],
    benchmark_name: str,
):
    """
    Export a single-config benchmark result to Langfuse.

    Creates a Dataset with one item per question and one Experiment Run
    with the pre-computed answers and RAGAS scores.
    """
    langfuse = _init_langfuse()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    if not results:
        logger.warning("No benchmark results to export to Langfuse.")
        return

    config_result = results[0]
    config = config_result.get("configuration", {})
    bench_cfg = config.get("services", {}).get("benchmarking", {})
    questions = config_result.get("single_question_results", {})

    dataset_name = f"archi-bench/{benchmark_name}-{timestamp}"
    logger.info("Creating Langfuse dataset: %s", dataset_name)

    label = f"{bench_cfg.get('agent_class', '?')}/{bench_cfg.get('provider', '?')}/{bench_cfg.get('model', '?')}"

    langfuse.create_dataset(
        name=dataset_name,
        description=f"Benchmark: {label}",
        metadata={
            "config": {
                "agent_class": bench_cfg.get("agent_class", ""),
                "model": bench_cfg.get("model", ""),
                "provider": bench_cfg.get("provider", ""),
            },
            "benchmark_name": benchmark_name,
            "created_at": timestamp,
        },
    )

    # Build ordered lists for replay
    question_keys = sorted(questions.keys())
    answers = []
    ragas_scores = []

    for i, key in enumerate(question_keys):
        q = questions[key]
        langfuse.create_dataset_item(
            dataset_name=dataset_name,
            input={"question": q.get("question", "")},
            expected_output={"answer": q.get("reference_answer", "")},
            metadata={"question_index": i},
        )
        answers.append(q.get("answer", ""))
        ragas_scores.append({
            m: q.get(m) for m in RAGAS_METRICS if m in q
        })

    dataset = langfuse.get_dataset(dataset_name)

    def task(*, item, **kwargs):
        idx = item.metadata.get("question_index", 0) if hasattr(item, "metadata") else item.get("metadata", {}).get("question_index", 0)
        return answers[idx]

    def make_evaluator(metric_name):
        def evaluator(*, input, output, expected_output, metadata, **kwargs):
            idx = metadata.get("question_index", 0)
            score = ragas_scores[idx].get(metric_name)
            if score is None or score != score:
                return None
            return Evaluation(name=metric_name, value=score)
        return evaluator

    evaluators = [make_evaluator(m) for m in RAGAS_METRICS]

    dataset.run_experiment(
        name=f"Benchmark: {label}",
        description=f"Single-config benchmark — {config_result.get('configuration_file', '')}",
        task=task,
        evaluators=evaluators,
        metadata={
            "config": {
                "agent_class": bench_cfg.get("agent_class", ""),
                "model": bench_cfg.get("model", ""),
                "provider": bench_cfg.get("provider", ""),
            },
        },
    )

    langfuse.flush()
    logger.info(
        "Langfuse export complete. Dataset: '%s'. "
        "Open your Langfuse UI to review the experiment.",
        dataset_name,
    )
