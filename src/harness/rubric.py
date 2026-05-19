"""Rubric loader.

One source of truth for the LLM-as-Judge rubric. Loads from
`src/harness/rubric.yaml` (or a path override) and exposes a
`Rubric` pydantic model.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    description: str
    weight: float = 0.25
    scale: int = 5

    @field_validator("weight")
    @classmethod
    def _weight_range(cls, v: float) -> float:
        if v < 0 or v > 1:
            raise ValueError("weight must be in [0, 1]")
        return v

    @field_validator("scale")
    @classmethod
    def _scale_positive(cls, v: int) -> int:
        if v < 2:
            raise ValueError("scale must be >= 2")
        return v


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    criteria: List[Criterion] = Field(default_factory=list)

    @field_validator("criteria")
    @classmethod
    def _weights_sum_to_one(cls, v: List[Criterion]) -> List[Criterion]:
        if not v:
            raise ValueError("rubric must have at least one criterion")
        total = sum(c.weight for c in v)
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"criterion weights must sum to 1.0 (got {total:.4f})")
        return v

    def criterion_ids(self) -> List[str]:
        return [c.id for c in self.criteria]


def _default_rubric_path() -> Path:
    return Path(__file__).parent / "rubric.yaml"


def load_rubric(path: Optional[Path | str] = None) -> Rubric:
    """Load the canonical rubric. Raises on missing file or invalid shape."""
    p = Path(path) if path else _default_rubric_path()
    if not p.exists():
        raise FileNotFoundError(f"rubric file not found: {p}")
    data = yaml.safe_load(p.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"rubric at {p} did not parse to a mapping")
    return Rubric.model_validate(data)


@lru_cache(maxsize=1)
def default_rubric() -> Rubric:
    """Cached default rubric load for hot paths."""
    return load_rubric()
