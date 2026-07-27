from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from .validation import DatasetItem, validate_gold_output


def prepare_dataset_items(
    items: Sequence[DatasetItem], evaluator: Any
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Prepare immutable gold atoms while keeping failures item-scoped."""
    prepared_rows: List[Dict[str, Any]] = []
    result_rows: List[Dict[str, Any]] = []
    for item in items:
        metadata = {
            "category": item.category,
            "answer_mode": item.answer_mode,
            "answer_source": item.answer_source,
        }
        if item.time_sensitive:
            result_rows.append(
                {
                    "item_id": item.id,
                    "status": "skipped_time_sensitive",
                    **metadata,
                }
            )
            continue
        try:
            if item.expected_atoms is not None:
                gold_atoms = item.expected_atoms
                atom_source = "supplied"
            else:
                gold_atoms = validate_gold_output(
                    evaluator.extract_gold(item.question, item.answer),
                    context=f"gold extraction for item {item.id}",
                )
                atom_source = "inferred"
        except Exception as exc:
            result_rows.append(
                {
                    "item_id": item.id,
                    "status": "preparation_failed",
                    "error": str(exc),
                    **metadata,
                }
            )
            continue
        prepared_rows.append(
            {
                "item_id": item.id,
                "question": item.question,
                "answer": item.answer,
                "time_sensitive": item.time_sensitive,
                **metadata,
                "atom_source": atom_source,
                "gold_atoms": [atom.to_dict() for atom in gold_atoms],
            }
        )
        result_rows.append({"item_id": item.id, "status": "prepared", **metadata})
    return prepared_rows, result_rows
