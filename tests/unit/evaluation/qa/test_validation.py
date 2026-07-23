# isort: skip_file
import pytest

from src.evaluation.qa.validation import (
    Atom,
    validate_dataset_rows,
    validate_judgments,
)  # isort: skip


class TestDatasetValidation:
    def test_accepts_strict_row_and_derives_stable_id(self):
        rows = [
            {
                "question": "What is the quota?\r\n",
                "expected_answer": "2.8 TB\r",
                "freshness": "static",
                "expected_atoms": [
                    {"id": "quota", "text": "The quota is 2.8 TB", "required": True}
                ],
            }
        ]

        first = validate_dataset_rows(rows)[0]
        second = validate_dataset_rows(rows)[0]

        assert first.id == second.id
        assert first.question == "What is the quota?\n"
        assert first.expected_answer == "2.8 TB\n"
        assert first.expected_atoms == [
            Atom(id="quota", text="The quota is 2.8 TB", required=True)
        ]

    def test_supplied_atom_text_is_not_rewritten(self):
        item = validate_dataset_rows(
            [
                {
                    "question": "Q",
                    "expected_answer": "A",
                    "freshness": "static",
                    "expected_atoms": [
                        {"id": "g1", "text": "line one\r\nline two", "required": True}
                    ],
                }
            ]
        )[0]

        assert item.expected_atoms[0].text == "line one\r\nline two"

    @pytest.mark.parametrize(
        "row,error",
        [
            (
                {
                    "question": "Q",
                    "expected_answer": "A",
                    "freshness": "static",
                    "category": "forbidden",
                },
                r"unknown field\(s\): category",
            ),
            (
                {"question": "Q", "expected_answer": "A", "freshness": "recent"},
                "freshness must be one of",
            ),
            (
                {
                    "question": "Q",
                    "expected_answer": "A",
                    "freshness": "static",
                    "expected_atoms": [
                        {"id": "optional", "text": "A", "required": False}
                    ],
                },
                "at least one required atom",
            ),
        ],
    )
    def test_rejects_invalid_rows(self, row, error):
        with pytest.raises(ValueError, match=error):
            validate_dataset_rows([row])

    def test_rejects_duplicate_derived_ids(self):
        row = {"question": "Q", "expected_answer": "A", "freshness": "static"}
        with pytest.raises(ValueError, match="duplicate or colliding id"):
            validate_dataset_rows([row, row])


class TestJudgmentValidation:
    def test_rejects_response_atom_evidence_fields(self):
        gold = [Atom(id="g1", text="gold", required=True)]
        raw = {
            "judgments": [
                {
                    "atom_id": "g1",
                    "outcome": "entailed",
                    "supporting_response_atom_ids": ["r1"],
                    "rationale": "match",
                }
            ]
        }

        with pytest.raises(
            ValueError, match=r"unknown field\(s\): supporting_response_atom_ids"
        ):
            validate_judgments(raw, gold_atoms=gold, context="comparison")


class TestJudgmentValidationCompleteness:
    def test_rejects_missing_gold_judgment(self):
        gold = [
            Atom(id="g1", text="one", required=True),
            Atom(id="g2", text="two", required=False),
        ]
        with pytest.raises(ValueError, match="missing judgment.*g2"):
            validate_judgments(
                {
                    "judgments": [
                        {
                            "atom_id": "g1",
                            "outcome": "not_mentioned",
                            "rationale": "missing",
                        }
                    ]
                },
                gold_atoms=gold,
                context="comparison",
            )

    def test_rejects_empty_rationale_for_any_outcome(self):
        with pytest.raises(ValueError, match="rationale must be a non-empty string"):
            validate_judgments(
                {
                    "judgments": [
                        {
                            "atom_id": "g1",
                            "outcome": "not_mentioned",
                            "rationale": "",
                        }
                    ]
                },
                gold_atoms=[Atom(id="g1", text="gold", required=True)],
                context="comparison",
            )
