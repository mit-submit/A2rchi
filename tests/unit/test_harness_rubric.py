"""Unit tests for the rubric loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.harness.rubric import Criterion, Rubric, default_rubric, load_rubric


def _valid_rubric_dict():
    return {
        "name": "test-rubric",
        "description": "unit test rubric",
        "criteria": [
            {"id": "relevance", "description": "...", "weight": 0.5, "scale": 5},
            {"id": "completeness", "description": "...", "weight": 0.5, "scale": 5},
        ],
    }


def test_default_rubric_loads_and_validates():
    r = default_rubric()
    assert isinstance(r, Rubric)
    assert len(r.criteria) >= 1
    assert "relevance" in r.criterion_ids()
    total = sum(c.weight for c in r.criteria)
    assert abs(total - 1.0) < 1e-6


def test_load_rubric_from_custom_path(tmp_path: Path):
    p = tmp_path / "rubric.yaml"
    p.write_text(yaml.safe_dump(_valid_rubric_dict()))
    r = load_rubric(p)
    assert r.name == "test-rubric"
    assert len(r.criteria) == 2


def test_rubric_rejects_weights_not_summing_to_one():
    bad = _valid_rubric_dict()
    bad["criteria"][0]["weight"] = 0.3
    # sum is 0.8
    with pytest.raises(ValidationError):
        Rubric.model_validate(bad)


def test_criterion_weight_out_of_range_rejected():
    with pytest.raises(ValidationError):
        Criterion(id="x", description="...", weight=1.5)


def test_criterion_scale_must_be_at_least_two():
    with pytest.raises(ValidationError):
        Criterion(id="x", description="...", weight=0.5, scale=1)


def test_rubric_requires_at_least_one_criterion():
    bad = _valid_rubric_dict()
    bad["criteria"] = []
    with pytest.raises(ValidationError):
        Rubric.model_validate(bad)


def test_load_rubric_missing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_rubric(tmp_path / "missing.yaml")


def test_load_rubric_non_mapping_rejected(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("- just\n- a list\n")
    with pytest.raises(ValueError):
        load_rubric(p)
