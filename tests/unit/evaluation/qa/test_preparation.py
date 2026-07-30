import pytest

from src.evaluation.qa.artifacts import write_jsonl
from src.evaluation.qa.preparation import (PreparationRecord,
                                           load_preparation_records,
                                           prepare_dataset_items)
from src.evaluation.qa.validation import Atom, DatasetItem


def _item(
    item_id,
    *,
    time_sensitive=False,
    expected_atoms=None,
):
    return DatasetItem(
        id=item_id,
        question=f"Question {item_id}",
        answer=f"Answer {item_id}",
        time_sensitive=time_sensitive,
        category="category",
        answer_mode="direct_answer",
        answer_source="source",
        expected_atoms=expected_atoms,
    )


class _Extractor:
    def extract_gold(self, question, answer):
        if answer.endswith("failed"):
            raise RuntimeError("extraction failed")
        return {
            "atoms": [
                {
                    "id": "A1",
                    "text": answer,
                    "required": True,
                }
            ]
        }


class TestPreparationRecord:
    @pytest.mark.parametrize(
        "record, message",
        [
            (
                lambda: PreparationRecord(
                    item=_item("prepared"),
                    status="prepared",
                    gold_atoms=None,
                    atom_source="inferred",
                ),
                "requires gold atoms",
            ),
            (
                lambda: PreparationRecord(
                    item=_item("failed"),
                    status="preparation_failed",
                ),
                "requires an error",
            ),
            (
                lambda: PreparationRecord(
                    item=_item("skipped", time_sensitive=True),
                    status="skipped_time_sensitive",
                    error="unexpected",
                ),
                "cannot contain output",
            ),
            (
                lambda: PreparationRecord(
                    item=_item("unknown", time_sensitive=True),
                    status="unknown",
                ),
                "unsupported preparation status",
            ),
            (
                lambda: PreparationRecord(
                    item=_item("prepared"),
                    status="prepared",
                    gold_atoms=(Atom(id="A1", text="answer", required=True),),
                    atom_source="unknown",
                ),
                "requires an atom source",
            ),
            (
                lambda: PreparationRecord(
                    item=_item(
                        "supplied",
                        expected_atoms=[
                            Atom(id="A1", text="answer", required=True)
                        ],
                    ),
                    status="prepared",
                    gold_atoms=(Atom(id="A1", text="answer", required=True),),
                    atom_source="inferred",
                ),
                "atom source does not match",
            ),
            (
                lambda: PreparationRecord(
                    item=_item(
                        "supplied",
                        expected_atoms=[
                            Atom(id="A1", text="answer", required=True)
                        ],
                    ),
                    status="preparation_failed",
                    error="unexpected",
                ),
                "supplied atoms cannot fail",
            ),
        ],
    )
    def test_rejects_status_field_mismatches(self, record, message):
        with pytest.raises(ValueError, match=message):
            record()

    def test_prepares_one_terminal_record_per_input(self):
        supplied_atom = Atom(id="S1", text="supplied", required=True)
        items = [
            _item("supplied", expected_atoms=[supplied_atom]),
            _item("inferred"),
            _item("skipped", time_sensitive=True),
            _item("failed"),
        ]

        records = prepare_dataset_items(items, _Extractor())

        assert [record.status for record in records] == [
            "prepared",
            "prepared",
            "skipped_time_sensitive",
            "preparation_failed",
        ]
        assert records[0].atom_source == "supplied"
        assert records[0].prepared_gold_atoms == (supplied_atom,)
        assert records[1].atom_source == "inferred"
        assert records[2].to_dict() == {
            "item_id": "skipped",
            "status": "skipped_time_sensitive",
            "category": "category",
            "answer_mode": "direct_answer",
            "answer_source": "source",
        }
        assert records[3].error == "extraction failed"


class TestPreparationArtifact:
    def test_round_trips_records_against_the_input_snapshot(self, tmp_path):
        supplied_atom = Atom(id="S1", text="supplied", required=True)
        items = [
            _item("supplied", expected_atoms=[supplied_atom]),
            _item("skipped", time_sensitive=True),
        ]
        records = prepare_dataset_items(items, _Extractor())
        path = tmp_path / "preparation.jsonl"
        write_jsonl(path, (record.to_dict() for record in records))

        loaded = load_preparation_records(path, items)

        assert loaded == records

    def test_rejects_duplicate_item_records(self, tmp_path):
        item = _item("item", time_sensitive=True)
        other = _item("other", time_sensitive=True)
        row = PreparationRecord(
            item=item,
            status="skipped_time_sensitive",
        ).to_dict()
        path = tmp_path / "preparation.jsonl"
        write_jsonl(path, [row, row])

        with pytest.raises(ValueError, match="duplicate item IDs"):
            load_preparation_records(path, [item, other])

    def test_rejects_fields_inconsistent_with_status(self, tmp_path):
        item = _item("item", time_sensitive=True)
        row = PreparationRecord(
            item=item,
            status="skipped_time_sensitive",
        ).to_dict()
        row["error"] = "not allowed"
        path = tmp_path / "preparation.jsonl"
        write_jsonl(path, [row])

        with pytest.raises(ValueError, match="invalid fields"):
            load_preparation_records(path, [item])

    def test_rejects_missing_or_unknown_item_records(self, tmp_path):
        item = _item("item", time_sensitive=True)
        unknown = _item("unknown", time_sensitive=True)
        path = tmp_path / "preparation.jsonl"
        write_jsonl(
            path,
            [
                PreparationRecord(
                    item=unknown,
                    status="skipped_time_sensitive",
                ).to_dict()
            ],
        )

        with pytest.raises(ValueError, match="does not match the input snapshot"):
            load_preparation_records(path, [item])

    def test_rejects_reordered_item_records(self, tmp_path):
        first = _item("first", time_sensitive=True)
        second = _item("second", time_sensitive=True)
        path = tmp_path / "preparation.jsonl"
        write_jsonl(
            path,
            [
                PreparationRecord(
                    item=second,
                    status="skipped_time_sensitive",
                ).to_dict(),
                PreparationRecord(
                    item=first,
                    status="skipped_time_sensitive",
                ).to_dict(),
            ],
        )

        with pytest.raises(ValueError, match="item order"):
            load_preparation_records(path, [first, second])
