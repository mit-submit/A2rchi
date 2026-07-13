from __future__ import annotations

from typing import Iterable, List

from src.data_manager.captioning.image_loader import IMAGE_EXTENSIONS

DEFAULT_ACCEPTED_FILES = [
    ".pdf",
    ".md",
    ".txt",
    ".docx",
    ".html",
    ".htm",
    ".json",
    ".yaml",
    ".yml",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".cpp",
    ".h",
    ".sh",
]


def _normalize_extensions(values: Iterable[str]) -> List[str]:
    normalized: List[str] = []
    for value in values:
        if not value:
            continue
        ext = str(value).strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = f".{ext}"
        if ext not in normalized:
            normalized.append(ext)
    return normalized


def get_effective_accepted_files(config: dict) -> List[str]:
    """Return upload extensions after captioning-aware expansion."""
    global_cfg = config.get("global", {}) or {}
    data_manager_cfg = config.get("data_manager", {}) or {}
    captioning_cfg = data_manager_cfg.get("captioning", {}) or {}

    if "ACCEPTED_FILES" in global_cfg:
        return _normalize_extensions(global_cfg.get("ACCEPTED_FILES") or [])

    accepted = _normalize_extensions(DEFAULT_ACCEPTED_FILES)

    if captioning_cfg.get("enabled", False):
        for ext in IMAGE_EXTENSIONS:
            if ext not in accepted:
                accepted.append(ext)

    return accepted
