from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, List, Tuple

from src.utils.logging import get_logger

logger = get_logger(__name__)


def _collect_local_paths(config: dict) -> Tuple[bool, List[Path]]:
    """Return (enabled, paths) from the config's local_files section."""
    local_cfg = (((config.get("data_manager") or {}).get("sources") or {}).get("local_files") or {})
    enabled = bool(local_cfg.get("enabled", True))
    raw_paths = local_cfg.get("paths") or []
    if isinstance(raw_paths, (str, Path)):
        raw_paths = [raw_paths]
    collected: List[Path] = []
    for entry in raw_paths:
        try:
            collected.append(Path(entry).expanduser())
        except TypeError:
            logger.warning("Skipping invalid local_files path entry: %r", entry)
    return enabled, collected


def _dest_relative(path: Path, staging_subdir: str) -> Path:
    """Compute the path inside the volume under staging_subdir that mirrors the host path."""
    expanded = path.expanduser()
    if expanded.is_absolute():
        rel = Path(*expanded.parts[1:])  # drop root slash
    else:
        rel = expanded
    return Path(staging_subdir) / rel


def _run_copy_command(cmd: List[str]) -> None:
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed ({result.returncode}): {result.stderr.strip()}")


def _copy_file(container_tool: str, volume_name: str, data_root: str, src: Path, dest_rel: Path) -> None:
    """Copy a single file into the target volume."""
    src_parent = src.parent
    cmd = [
        container_tool,
        "run",
        "--rm",
        "-v",
        f"{volume_name}:{data_root}",
        "-v",
        f"{src_parent}:/src:ro",
        "busybox",
        "sh",
        "-c",
        f"mkdir -p {data_root}/{dest_rel.parent}; cp -r /src/{src.name} {data_root}/{dest_rel}",
    ]
    _run_copy_command(cmd)


def _copy_dir(container_tool: str, volume_name: str, data_root: str, src: Path, dest_rel: Path) -> None:
    """Copy a directory into the target volume, preserving the directory name."""
    cmd = [
        container_tool,
        "run",
        "--rm",
        "-v",
        f"{volume_name}:{data_root}",
        "-v",
        f"{src}:/src:ro",
        "busybox",
        "sh",
        "-c",
        f"mkdir -p {data_root}/{dest_rel.parent}; cp -r /src {data_root}/{dest_rel}",
    ]
    _run_copy_command(cmd)


def stage_local_files_to_volume(
    *,
    config: dict,
    volume_name: str,
    container_tool: str,
    staging_subdir: str = "raw_local_files",
) -> None:
    """Stage configured local files into the data-manager volume before containers start."""
    data_root = "/data"
    try:
        configured_root = (config.get("global") or {}).get("DATA_PATH")
        if configured_root:
            root_path = Path(configured_root)
            if root_path.is_absolute():
                data_root = root_path.as_posix()
            else:
                data_root = f"/data/{root_path.as_posix()}"
    except Exception:
        logger.debug("Falling back to default data root for staging; unable to read global.DATA_PATH.")

    enabled, host_paths = _collect_local_paths(config)
    if not enabled:
        logger.info("local_files disabled; skipping staging.")
        return
    if not host_paths:
        logger.info("No local_files.paths specified; skipping staging.")
        return
    if not volume_name:
        logger.warning("No volume name available for data-manager; cannot stage local files.")
        return

    logger.info("Staging local files into volume '%s' under %s", volume_name, staging_subdir)
    for host_path in host_paths:
        if not host_path.exists():
            logger.warning("Host path missing, skipping: %s", host_path)
            continue
        dest_rel = _dest_relative(host_path, staging_subdir)
        try:
            if host_path.is_dir():
                _copy_dir(container_tool, volume_name, data_root, host_path, dest_rel)
            else:
                _copy_file(container_tool, volume_name, data_root, host_path, dest_rel)
        except Exception as exc:
            logger.error("Failed to stage %s: %s", host_path, exc)
