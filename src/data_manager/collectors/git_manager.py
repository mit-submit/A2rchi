# src/data_manager/collectors/git_manager.py
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from git import Repo

from src.data_manager.collectors.git_resource import GitResource
from src.data_manager.collectors.persistence import PersistenceService
from src.utils.config_access import get_global_config
from src.utils.env import read_secret
from src.utils.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_CODE_SUFFIXES = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs",
    ".c", ".cpp", ".h", ".hpp", ".sh", ".sql",
    ".json", ".yaml", ".yml", ".toml", ".md", ".txt",
}
_DEFAULT_EXCLUDE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    ".idea", ".vscode", "dist", "build",
}


class GitManager:
    """
    Collects git repositories (MkDocs docs + code files) into the shared data path.

    Interface mirrors LocalFileManager — instantiate with dm_config, then call
    collect_all_from_config(persistence) or collect(urls, persistence) directly.
    """

    def __init__(self, dm_config: Optional[Dict[str, Any]] = None) -> None:
        global_config = get_global_config()
        self.data_path = Path(global_config["DATA_PATH"])

        sources_config = (dm_config or {}).get("sources", {}) or {}
        self.config: Dict[str, Any] = (
            dict(sources_config.get("git", {}))
            if isinstance(sources_config, dict)
            else {}
        )

        self.enabled = self.config.get("enabled", True)
        self.git_dir = Path(self.data_path) / "raw_git_repos"
        self.git_dir.mkdir(parents=True, exist_ok=True)

        self.code_suffixes = {
            s.lower()
            for s in self.config.get("code_suffixes", _DEFAULT_CODE_SUFFIXES)
        }
        self.exclude_dirs = set(
            self.config.get("exclude_dirs", _DEFAULT_EXCLUDE_DIRS)
        )
        self.max_file_size_bytes = int(
            self.config.get("max_file_size_bytes", 1_000_000)
        )

        self.git_username = read_secret("GIT_USERNAME")
        self.git_token = read_secret("GIT_TOKEN")
        self._credentials_available = bool(self.git_username and self.git_token)
        if not self._credentials_available:
            logger.info("No git credentials supplied; will attempt public repo cloning.")

    # ── Public interface (mirrors LocalFileManager) ───────────────────────────

    def collect_all_from_config(self, persistence: PersistenceService) -> None:
        if not self.enabled:
            return
        urls: List[str] = self.config.get("urls", [])
        if not urls:
            logger.info("No git URLs configured; skipping")
            return
        self.collect(urls, persistence)

    def schedule_collect_git(
        self, persistence: PersistenceService, last_run: Optional[str] = None
    ) -> None:
        """Re-harvest all repos known to the catalog (config + dynamically added)."""
        metadata = persistence.catalog.get_metadata_by_filter(
            "source_type", source_type="git", metadata_keys=["repo_url"]
        )
        urls = list({m[1]["repo_url"] for m in metadata if m[1].get("repo_url")})
        if not urls:
            return
        self.collect(urls, persistence)

    def collect(self, git_urls: List[str], persistence: PersistenceService) -> None:
        """Collect a list of git URLs and persist each harvested file."""
        if not git_urls:
            logger.warning("No git URLs provided; skipping")
            return

        for url in git_urls:
            try:
                repo_info = self._prepare_repository(url)
            except ValueError as exc:
                logger.info("%s", exc)
                continue
            except Exception as exc:
                logger.error("Failed to clone %s: %s", url, exc)
                continue

            try:
                target_dir = self.data_path / "git" / repo_info["repo_name"]
                for resource in self._harvest_repository(repo_info):
                    self._persist_one(resource, persistence, target_dir)
            finally:
                shutil.rmtree(repo_info["repo_path"], ignore_errors=True)

        logger.info("Git collection complete")

    # ── Internal harvest ──────────────────────────────────────────────────────

    def _harvest_repository(self, repo_info: Dict[str, Any]) -> Iterator[GitResource]:
        yield from self._harvest_mkdocs(repo_info)
        yield from self._harvest_code(repo_info)

    def _harvest_mkdocs(self, repo_info: Dict[str, Any]) -> Iterator[GitResource]:
        repo_path: Path = repo_info["repo_path"]
        docs_dir = repo_path / "docs"
        if not docs_dir.exists():
            logger.info("Skipping MkDocs harvest for %s; no docs/ dir", repo_path)
            return

        mkdocs_site_url: Optional[str] = repo_info["mkdocs_site_url"]
        base_url: str = repo_info["web_base_url"]
        ref: str = repo_info["ref"]
        repo_name: str = repo_info["repo_name"]
        repo_url: str = repo_info["repo_url"]

        for md_path in docs_dir.rglob("*.md"):
            if mkdocs_site_url:
                url = mkdocs_site_url + md_path.relative_to(docs_dir).with_suffix("").as_posix()
            else:
                url = self._build_blob_url(base_url, ref, md_path.relative_to(repo_path))

            text = md_path.read_text(encoding="utf-8", errors="ignore")
            if not text.strip():
                logger.info("Skipping empty doc: %s", md_path)
                continue

            yield GitResource(
                repo_url=repo_url,
                file_path=str(Path(repo_name) / md_path.relative_to(repo_path)),
                content=text,
                source_type="git",
                branch=repo_info.get("branch", ""),
                ref=ref,
                title=md_path.stem.replace("_", " ").replace("-", " ").title(),
            )

    def _harvest_code(self, repo_info: Dict[str, Any]) -> Iterator[GitResource]:
        repo_path: Path = repo_info["repo_path"]
        base_url: str = repo_info["web_base_url"]
        ref: str = repo_info["ref"]
        repo_name: str = repo_info["repo_name"]
        repo_url: str = repo_info["repo_url"]

        for file_path in self._iter_code_files(repo_path):
            rel = file_path.relative_to(repo_path)

            # avoid overlap with _harvest_mkdocs
            if rel.parts and rel.parts[0] == "docs" and file_path.suffix.lower() == ".md":
                continue

            if not self._is_allowed_suffix(file_path):
                continue

            try:
                if file_path.stat().st_size > self.max_file_size_bytes:
                    logger.warning("Skipping %s — exceeds size limit", file_path)
                    continue
            except OSError:
                continue

            if self._looks_binary(file_path):
                continue

            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue

            if not text.strip():
                continue

            yield GitResource(
                repo_url=repo_url,
                file_path=str(Path(repo_name) / rel),
                content=text,
                source_type="git",
                branch=repo_info.get("branch", ""),
                ref=ref,
                title=None,
            )

    # ── Repository preparation ────────────────────────────────────────────────

    def _prepare_repository(self, url: str) -> Dict[str, Any]:
        url_dict = self._parse_url(url)
        repo_path = self._clone_repo(url_dict)
        return {
            "repo_path": repo_path,
            "repo_name": url_dict["repo_name"],
            "repo_url": url_dict["original_url"],
            "branch": url_dict["branch"] or "",
            "mkdocs_site_url": self._read_mkdocs_site_url(repo_path),
            "ref": self._determine_ref(repo_path, url_dict["branch"]),
            "web_base_url": self._compute_web_base_url(url_dict["original_url"]),
        }

    def _parse_url(self, url: str) -> Dict[str, Any]:
        match = re.search(
            r"(?:github|gitlab)\.[\w.]+\/[^\/]+\/([\w.-]+)(?:\.git|\/|$)",
            url, re.IGNORECASE,
        )
        if not match:
            raise ValueError(f"Git URL does not match expected format: {url}")
        repo_name = match.group(1)

        if self._credentials_available:
            if "gitlab" in url:
                clone_url = url.replace("gitlab", f"{self.git_username}:{self.git_token}@gitlab")
            elif "github" in url:
                clone_url = url.replace("github", f"{self.git_username}:{self.git_token}@github")
            else:
                clone_url = url
        else:
            clone_url = url

        branch = None
        parts = re.split(r"/(?:-/)?tree/", clone_url, maxsplit=1)
        if len(parts) > 1:
            branch = parts[1].strip("/") or None
            clone_url = parts[0].rstrip("/")

        return {"original_url": url, "clone_url": clone_url, "repo_name": repo_name, "branch": branch}

    def _clone_repo(self, url_dict: Dict[str, Any]) -> Path:
        repo_path = self.git_dir / url_dict["repo_name"]
        logger.info("Cloning %s …", url_dict["repo_name"])
        kwargs = {}
        if url_dict["branch"]:
            kwargs["branch"] = url_dict["branch"]
        Repo.clone_from(url_dict["clone_url"], repo_path, **kwargs)
        return repo_path

    def _read_mkdocs_site_url(self, repo_path: Path) -> Optional[str]:
        mkdocs_file = repo_path / "mkdocs.yml"
        if not mkdocs_file.exists():
            return None
        try:
            from mkdocs.utils.yaml import yaml_load
            with mkdocs_file.open() as f:
                data = yaml_load(f)
            site_url = data.get("site_url")
            if not site_url:
                return None
            return site_url if site_url.endswith("/") else site_url + "/"
        except Exception:
            return None

    def _compute_web_base_url(self, url: str) -> str:
        sanitized = re.sub(r"//[^@/]+@", "//", url)
        sanitized = re.split(r"/(?:-/)?tree/", sanitized, maxsplit=1)[0]
        return sanitized.rstrip("/").removesuffix(".git")

    def _determine_ref(self, repo_path: Path, branch: Optional[str]) -> str:
        if branch:
            return branch
        try:
            return Repo(repo_path).active_branch.name
        except Exception:
            try:
                return Repo(repo_path).head.commit.hexsha[:7]
            except Exception:
                return "main"

    def _build_blob_url(self, base_url: str, ref: str, rel: Path) -> str:
        base = base_url.rstrip("/")
        if "gitlab" in base:
            return f"{base}/-/blob/{ref}/{rel.as_posix()}"
        return f"{base}/blob/{ref}/{rel.as_posix()}"

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _iter_code_files(self, repo_path: Path) -> Iterator[Path]:
        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in self.exclude_dirs]
            for name in files:
                yield Path(root) / name

    def _is_allowed_suffix(self, path: Path) -> bool:
        return path.suffix.lower() in self.code_suffixes

    def _looks_binary(self, path: Path) -> bool:
        try:
            return b"\0" in path.open("rb").read(8000)
        except Exception:
            return True

    def _persist_one(
        self,
        resource: GitResource,
        persistence: PersistenceService,
        target_dir: Path,
    ) -> None:
        try:
            persistence.persist_resource(resource, target_dir)
        except Exception as exc:
            logger.warning("Failed to persist %s: %s", resource.file_path, exc)