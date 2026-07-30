from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import (Any, Dict, List, Literal, Optional, Protocol, Sequence,
                    Set, Tuple)

from .artifacts import read_jsonl
from .validation import Atom, DatasetItem, validate_atoms, validate_gold_output

PreparationStatus = Literal[
    "prepared",
    "skipped_time_sensitive",
    "preparation_failed",
]
AtomSource = Literal["supplied", "inferred"]


class GoldExtractor(Protocol):
    def extract_gold(self, question: str, answer: str) -> object:
        ...


class AnswerComparator(Protocol):
    def compare(
        self,
        question: str,
        gold_atoms: Sequence[Atom],
        answer: str,
    ) -> object:
        ...


@dataclass(frozen=True)
class PreparationRecord:
    item: DatasetItem
    status: PreparationStatus
    gold_atoms: Optional[Tuple[Atom, ...]] = None
    atom_source: Optional[AtomSource] = None
    error: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in {
            "prepared",
            "skipped_time_sensitive",
            "preparation_failed",
        }:
            raise ValueError("unsupported preparation status")
        if self.status == "prepared":
            if self.item.time_sensitive:
                raise ValueError("time-sensitive item cannot be prepared")
            if not self.gold_atoms:
                raise ValueError("prepared record requires gold atoms")
            if self.atom_source not in {"supplied", "inferred"}:
                raise ValueError("prepared record requires an atom source")
            expected_source = (
                "supplied" if self.item.expected_atoms is not None else "inferred"
            )
            if self.atom_source != expected_source:
                raise ValueError("prepared record atom source does not match its item")
            if (
                self.atom_source == "supplied"
                and tuple(self.item.expected_atoms or ()) != self.gold_atoms
            ):
                raise ValueError("prepared record atoms do not match supplied atoms")
            if self.error is not None:
                raise ValueError("prepared record cannot contain an error")
            return
        if self.status == "preparation_failed":
            if self.item.time_sensitive:
                raise ValueError("time-sensitive item cannot fail preparation")
            if self.item.expected_atoms is not None:
                raise ValueError("item with supplied atoms cannot fail preparation")
            if self.error is None:
                raise ValueError("failed preparation requires an error")
            if not isinstance(self.error, str):
                raise ValueError("failed preparation error must be a string")
            if self.gold_atoms is not None or self.atom_source is not None:
                raise ValueError("failed preparation cannot contain prepared output")
            return
        if not self.item.time_sensitive:
            raise ValueError("only a time-sensitive item can be skipped")
        if (
            self.gold_atoms is not None
            or self.atom_source is not None
            or self.error is not None
        ):
            raise ValueError("skipped preparation cannot contain output")

    @property
    def prepared_gold_atoms(self) -> Tuple[Atom, ...]:
        if self.status != "prepared" or self.gold_atoms is None:
            raise ValueError("preparation record is not prepared")
        return self.gold_atoms

    def to_dict(self) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "item_id": self.item.id,
            "status": self.status,
            "category": self.item.category,
            "answer_mode": self.item.answer_mode,
            "answer_source": self.item.answer_source,
        }
        if self.status == "prepared":
            row.update(
                {
                    "question": self.item.question,
                    "answer": self.item.answer,
                    "time_sensitive": self.item.time_sensitive,
                    "atom_source": self.atom_source,
                    "gold_atoms": [
                        atom.to_dict() for atom in self.prepared_gold_atoms
                    ],
                }
            )
        elif self.status == "preparation_failed":
            row["error"] = self.error
        return row


def prepare_dataset_items(
    items: Sequence[DatasetItem], extractor: GoldExtractor
) -> List[PreparationRecord]:
    """Prepare one immutable terminal record for every validated dataset item."""
    records: List[PreparationRecord] = []
    for item in items:
        if item.time_sensitive:
            records.append(
                PreparationRecord(item=item, status="skipped_time_sensitive")
            )
            continue
        try:
            if item.expected_atoms is not None:
                gold_atoms = item.expected_atoms
                atom_source: AtomSource = "supplied"
            else:
                gold_atoms = validate_gold_output(
                    extractor.extract_gold(item.question, item.answer),
                    context=f"gold extraction for item {item.id}",
                )
                atom_source = "inferred"
        except Exception as exc:
            records.append(
                PreparationRecord(
                    item=item,
                    status="preparation_failed",
                    error=str(exc),
                )
            )
            continue
        records.append(
            PreparationRecord(
                item=item,
                status="prepared",
                gold_atoms=tuple(gold_atoms),
                atom_source=atom_source,
            )
        )
    return records


def _require_exact_keys(
    row: Dict[str, Any], expected: Set[str], *, context: str
) -> None:
    missing = sorted(expected - set(row))
    unknown = sorted(set(row) - expected)
    details = []
    if missing:
        details.append("missing: " + ", ".join(missing))
    if unknown:
        details.append("unknown: " + ", ".join(unknown))
    if details:
        raise ValueError(f"{context} has invalid fields ({'; '.join(details)})")


def _record_from_row(
    row: Dict[str, Any], item: DatasetItem, *, index: int
) -> PreparationRecord:
    context = f"preparation row {index}"
    status = row.get("status")
    if status not in {
        "prepared",
        "skipped_time_sensitive",
        "preparation_failed",
    }:
        raise ValueError(f"{context} has an unsupported status")
    base_fields = {
        "item_id",
        "status",
        "category",
        "answer_mode",
        "answer_source",
    }
    status_fields = {
        "prepared": {
            "question",
            "answer",
            "time_sensitive",
            "atom_source",
            "gold_atoms",
        },
        "preparation_failed": {"error"},
        "skipped_time_sensitive": set(),
    }[status]
    _require_exact_keys(row, base_fields | status_fields, context=context)
    if row["item_id"] != item.id:
        raise ValueError(f"{context} item ID does not match the input snapshot")
    for field in ("category", "answer_mode", "answer_source"):
        if row[field] != getattr(item, field):
            raise ValueError(f"{context} {field} does not match the input snapshot")

    if status == "prepared":
        if (
            row["question"] != item.question
            or row["answer"] != item.answer
            or row["time_sensitive"] is not item.time_sensitive
        ):
            raise ValueError(f"{context} does not match the input snapshot")
        atom_source = row["atom_source"]
        gold_atoms = tuple(
            validate_atoms(row["gold_atoms"], context=f"{context}.gold_atoms")
        )
        return PreparationRecord(
            item=item,
            status="prepared",
            gold_atoms=gold_atoms,
            atom_source=atom_source,
        )

    if status == "preparation_failed":
        return PreparationRecord(
            item=item,
            status="preparation_failed",
            error=row["error"],
        )
    return PreparationRecord(item=item, status="skipped_time_sensitive")


def load_preparation_records(
    path: Path, items: Sequence[DatasetItem]
) -> List[PreparationRecord]:
    rows = read_jsonl(path)
    if len(rows) != len(items):
        raise ValueError(
            "preparation artifact must contain exactly one row per input item"
        )
    item_ids = [item.id for item in items]
    row_ids = [row.get("item_id") for row in rows]
    if any(not isinstance(item_id, str) for item_id in row_ids):
        raise ValueError("preparation artifact contains an invalid item ID")
    if len(set(row_ids)) != len(row_ids):
        raise ValueError("preparation artifact contains duplicate item IDs")
    missing = sorted(set(item_ids) - set(row_ids))
    unknown = sorted(set(row_ids) - set(item_ids))
    if missing or unknown:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        raise ValueError(
            "preparation artifact does not match the input snapshot ("
            + "; ".join(details)
            + ")"
        )
    if row_ids != item_ids:
        raise ValueError("preparation artifact item order does not match the input")
    return [
        _record_from_row(row, item, index=index)
        for index, (row, item) in enumerate(zip(rows, items), 1)
    ]
