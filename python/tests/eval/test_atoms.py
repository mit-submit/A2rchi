"""archi.eval.atoms — dataset loading + validation (W5 foundation)."""
import json

import pytest

from archi.eval.atoms import (
    DatasetError,
    answer_sha256,
    load_dataset,
    resolve_json_pointer,
    validate_atom,
    validate_atoms,
)

MINIMAL = {
    "id": "q1",
    "question": "What?",
    "checks": [{"kind": "exact", "value": "42"}],
}


def _variant(**overrides):
    raw = {**MINIMAL, **overrides}
    return {key: value for key, value in raw.items() if value is not None}


def test_smoke_fixture_loads(smoke_dataset):
    atoms = load_dataset(smoke_dataset)
    assert len(atoms) == 7
    by_id = {atom.id: atom for atom in atoms}
    live = by_id["live-open-downtimes"]
    assert live.is_live
    assert live.oracle.calls[0].tool == "archi_gocdb_open_downtimes"
    assert live.checks[0].value_from == "/c1/count"
    graded = by_id["xrootd-fallback"]
    assert [fact.required for fact in graded.gold_facts] == [True, True, False]
    assert not by_id["cmssw-latest-14x"].is_live


def test_json_dataset_round_trip(tmp_path, smoke_dataset):
    atoms = load_dataset(smoke_dataset)
    path = tmp_path / "atoms.json"
    path.write_text(json.dumps([atom.to_dict() for atom in atoms]))
    reloaded = load_dataset(path)
    assert [atom.id for atom in reloaded] == [atom.id for atom in atoms]
    assert reloaded == atoms


def test_top_level_object_with_schema_version(tmp_path):
    path = tmp_path / "atoms.json"
    path.write_text(
        json.dumps({"schema_version": "archi-eval-v1", "atoms": [MINIMAL]})
    )
    assert load_dataset(path)[0].id == "q1"
    path.write_text(json.dumps({"schema_version": "other", "atoms": [MINIMAL]}))
    with pytest.raises(DatasetError, match="schema_version"):
        load_dataset(path)


@pytest.mark.parametrize(
    "raw, message",
    [
        (_variant(id=None), r"atom\.id.*must be a non-empty string"),
        (_variant(question="  "), r"question.*must be a non-empty string"),
        (_variant(bogus=1), "unknown field"),
        (_variant(checks=None, gold_facts=None), "at least one scoring criterion"),
        (_variant(checks=[{"kind": "fuzzy", "value": "x"}]), "must be one of"),
        (_variant(checks=[{"kind": "regex", "value": "["}]), "not a valid regex"),
        (
            _variant(checks=[{"kind": "exact"}]),
            "exactly one of 'value' or 'value_from'",
        ),
        (
            _variant(checks=[{"kind": "exact", "value": "a", "value_from": "/b"}]),
            "exactly one of 'value' or 'value_from'",
        ),
        (
            _variant(checks=[{"kind": "exact", "value_from": "/x"}]),
            "only valid on a live atom",
        ),
        (
            _variant(checks=[{"kind": "exact", "value": "x", "case_sensitive": 1}]),
            "case_sensitive.*must be a boolean",
        ),
        (_variant(tags="oops"), "tags.*must be a list"),
        (
            _variant(gold_facts=[{"id": "A", "text": "t"}]),
            "required.*must be a boolean",
        ),
        (
            _variant(
                gold_facts=[
                    {"id": "A", "text": "t", "required": False},
                ]
            ),
            "at least one required gold fact",
        ),
        (
            _variant(
                gold_facts=[
                    {"id": "A", "text": "t", "required": True},
                    {"id": "A", "text": "u", "required": False},
                ]
            ),
            "duplicate gold fact id",
        ),
        (
            _variant(oracle={"kind": "sql", "calls": []}),
            "oracle.kind.*must be 'mcp'",
        ),
        (
            _variant(oracle={"kind": "mcp", "calls": []}),
            "calls.*must be a non-empty list",
        ),
        (
            _variant(
                oracle={
                    "kind": "mcp",
                    "calls": [
                        {"id": "c1", "tool": "t"},
                        {"id": "c1", "tool": "u"},
                    ],
                }
            ),
            "duplicate call id",
        ),
        (
            _variant(
                oracle={
                    "kind": "mcp",
                    "calls": [{"id": "c1", "tool": "t", "answer_fields": {"n": "x"}}],
                }
            ),
            "RFC 6901",
        ),
    ],
)
def test_validation_errors_are_contextual(raw, message):
    with pytest.raises(DatasetError, match=message):
        validate_atom(raw)


def test_duplicate_atom_ids_rejected():
    with pytest.raises(DatasetError, match="duplicate atom id 'q1'"):
        validate_atoms([MINIMAL, dict(MINIMAL)])


def test_dataset_file_errors(tmp_path):
    with pytest.raises(DatasetError, match="existing file"):
        load_dataset(tmp_path / "missing.yaml")
    bad_suffix = tmp_path / "atoms.txt"
    bad_suffix.write_text("[]")
    with pytest.raises(DatasetError, match="must use .json, .yaml, or .yml"):
        load_dataset(bad_suffix)
    bad_json = tmp_path / "broken.json"
    bad_json.write_text("{nope")
    with pytest.raises(DatasetError, match="invalid JSON dataset"):
        load_dataset(bad_json)
    empty = tmp_path / "empty.yaml"
    empty.write_text("[]")
    with pytest.raises(DatasetError, match="at least one atom"):
        load_dataset(empty)


def test_json_pointer_resolution():
    payload = {"a": [{"b/c": 1}, {"~d": [10, 20]}]}
    assert resolve_json_pointer(payload, "/a/0/b~1c") == 1
    assert resolve_json_pointer(payload, "/a/1/~0d/1") == 20
    assert resolve_json_pointer(payload, "") == payload
    for pointer in ("/a/2", "/a/01", "/missing", "/a/0/b~1c/x"):
        with pytest.raises(ValueError, match="does not exist|invalid"):
            resolve_json_pointer(payload, pointer)


def test_answer_sha256_is_order_insensitive():
    left = answer_sha256({"c1": {"x": 1, "y": 2}})
    right = answer_sha256({"c1": {"y": 2, "x": 1}})
    assert left == right
    assert left != answer_sha256({"c1": {"x": 1, "y": 3}})
    with pytest.raises(ValueError):
        answer_sha256({})
