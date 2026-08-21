"""Cache preflight/health report helpers for optional cache-backed sources.

Ported from okg-deployments ``cms/cms_sources/_cache.py`` at
``main@f33a9c4`` (req.w2.sources-catalogs) — the ``cache_preflight_result``
/ ``cache_source_health`` pair that ``archi/auth/cache.py`` deliberately
left behind ("they move with the source ports that consume them").
Consumed by :mod:`archi.sources.dbs`, :mod:`archi.sources.conddb`, and
:mod:`archi.sources.wmstats`. Only change from the original: path
resolution takes the explicit ``base`` argument of
:func:`archi.auth.cache.resolve_repo_path` instead of assuming a
deployments-repo checkout.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from okg.substrate.library.sources.base import (
    SourceHealth,
    SourcePreflightResult,
)

from archi.auth.cache import content_hash, resolve_repo_path


def cache_preflight_result(
    *,
    source_name: str,
    description: str,
    cache_paths: Iterable[str | Path],
    records: Iterable[Any] | None,
    required: bool = False,
    mode: str = "cache",
    base: str | None = None,
) -> SourcePreflightResult:
    paths = tuple(cache_paths)
    resolved_paths = tuple(resolve_repo_path(p, base=base) for p in paths)
    missing = [p for p in resolved_paths if not p.is_file()]
    if missing:
        return SourcePreflightResult(
            source_name=source_name,
            status="cache_missing",
            mode=mode,
            required=required,
            cache_path=", ".join(str(p) for p in missing),
            reason=f"{description} cache file is missing",
            checked_at=_checked_at(),
        )
    count = len(tuple(records or ()))
    return SourcePreflightResult(
        source_name=source_name,
        status=_cache_status(count),
        mode=mode,
        required=required,
        record_count=count,
        content_hash=content_hash(paths, base=base),
        reason=_cache_reason(description, count, observed=True),
        checked_at=_checked_at(),
    )


def cache_source_health(
    *,
    description: str,
    cache_paths: Iterable[str | Path],
    record_count: int,
    base: str | None = None,
) -> SourceHealth:
    return SourceHealth(
        status=_cache_status(record_count),
        mode="cache",
        record_count=record_count,
        content_hash=content_hash(cache_paths, base=base),
        reason=_cache_reason(description, record_count, observed=False),
        checked_at=_checked_at(),
    )


def _cache_status(record_count: int) -> str:
    return "ok" if record_count else "skipped_optional"


def _cache_reason(
    description: str,
    record_count: int,
    *,
    observed: bool,
) -> str:
    if record_count:
        action = "observed" if observed else "used"
        return f"local {description} cache {action}"
    return (
        f"local {description} cache is present but empty; "
        "no live data fetch attempted"
    )


def _checked_at() -> str:
    return datetime.now(timezone.utc).isoformat()
