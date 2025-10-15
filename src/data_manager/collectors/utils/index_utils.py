from __future__ import annotations

from pathlib import Path
from typing import Dict

import yaml

from src.utils.logging import get_logger

logger = get_logger(__name__)


def load_index(data_path: Path | str, filename: str = "index.yaml") -> Dict[str, str]:
    """
    Load the unified resource index from the provided YAML file located within ``data_path``.
    Returns an empty mapping if the file does not exist or cannot be parsed.
    """
    base_path = Path(data_path)
    index_path = base_path / filename

    if not index_path.exists():
        return {}

    try:
        with index_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        logger.warning(f"Failed to parse {index_path}: {exc}")
        return {}

    if not isinstance(data, dict):
        logger.warning(f"{index_path} does not contain a mapping; defaulting to empty index.")
        return {}

    sanitized: Dict[str, str] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            logger.warning(f"Ignoring non-string resource key in {index_path}: {key!r}")
            continue
        if not isinstance(value, str):
            logger.warning(f"Ignoring non-string path for resource {key!r} in {index_path}: {value!r}")
            continue
        sanitized[key] = value

    return sanitized


def load_sources_catalog(data_path: Path | str) -> Dict[str, str]:
    """
    Convenience helper that returns the resource index mapping with absolute paths.
    """
    base_path = Path(data_path)
    index = load_index(base_path)
    resolved: Dict[str, str] = {}
    for key, stored_path in index.items():
        path = Path(stored_path)
        if not path.is_absolute():
            path = (base_path / path).resolve()
        resolved[key] = str(path)
    return resolved


def write_index(
    data_path: Path | str, index_data: Dict[str, str], filename: str = "index.yaml"
) -> None:
    """
    Persist the provided index to the given YAML file inside ``data_path``.
    """
    base_path = Path(data_path)
    index_path = base_path / filename
    index_path.parent.mkdir(parents=True, exist_ok=True)

    with index_path.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(index_data, fh, sort_keys=True)
