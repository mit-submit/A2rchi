from __future__ import annotations

import json as _json
import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Optional, Dict, List, Tuple
from functools import lru_cache, wraps
import secrets
import re

from flask import Flask, jsonify, redirect, render_template, request, url_for, session, flash
from flask_cors import CORS

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.localfile_manager import LocalFileManager
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager
from src.data_manager.collectors.utils.catalog_postgres import PostgresCatalogService
from src.data_manager.collectors.tickets.ticket_manager import TicketManager
from src.data_manager.vectorstore.loader_utils import load_text_from_path
from src.interfaces.chat_app.document_utils import check_credentials
from src.utils.env import read_secret
from src.utils.logging import get_logger
from src.data_manager.collectors.utils.catalog_postgres import _METADATA_COLUMN_MAP
from src.utils.config_access import get_full_config

logger = get_logger(__name__)


class FlaskAppWrapper:
    """Uploader UI + API wrapper for the data manager service."""

    def __init__(
        self,
        app: Flask,
        *,
        post_update_hook: Optional[Callable[[], None]] = None,
        status_file: Optional[Path] = None,
    ) -> None:
        self.app = app
        self.config = get_full_config()
        self.global_config = self.config["global"]
        self.services_config = self.config["services"]

        self.data_path = self.global_config["DATA_PATH"]
        self.pg_config = {
            "password": read_secret("PG_PASSWORD"),
            **self.services_config["postgres"],
        }
        self.persistence = PersistenceService(self.data_path, pg_config=self.pg_config)
        self.catalog = PostgresCatalogService(self.data_path, pg_config=self.pg_config)
        self.status_file = status_file or (Path(self.data_path) / "ingestion_status.json")

        secret_key = read_secret("FLASK_UPLOADER_APP_SECRET_KEY") or secrets.token_hex(32)
        self.app.secret_key = secret_key
        self.app.config["SESSION_COOKIE_NAME"] = "uploader_session"
        self.app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB upload limit

        self.auth_config = (self.services_config or {}).get("data_manager", {}).get("auth", {}) or {}
        self.auth_enabled = bool(self.auth_config.get("enabled", False))
        self.api_token = read_secret("DM_API_TOKEN") or None
        self.admin_users = {
            user.strip().lower()
            for user in (self.auth_config.get("admins") or [])
            if user and user.strip()
        }
        self.default_admin_user = (self.auth_config.get("default_admin_user") or "admin").strip()
        self.default_admin_password = read_secret("DM_ADMIN_PASSWD")
        self.salt = read_secret("UPLOADER_SALT")
        self.accounts_path = self.global_config.get("ACCOUNTS_PATH")
        if self.auth_enabled:
            if not self.accounts_path:
                logger.warning("ACCOUNTS_PATH not configured; only default auth account avilable. Set is as DM_ADMIN_PASSWD in your secrets file.")
                self.auth_enabled = True
            else:
                os.makedirs(self.accounts_path, exist_ok=True)
                if not self.salt:
                    logger.warning("UPLOADER_SALT not set; account checks may fail.")

        self.scraper_manager = ScraperManager(dm_config=self.config.get("data_manager"))
        self.ticket_manager = TicketManager(dm_config=self.config.get("data_manager"))
        self.localfile_manager = LocalFileManager(dm_config=self.config.get("data_manager"))
        self.post_update_hook = post_update_hook

        CORS(self.app)

        protected = self.require_admin
        self.add_endpoint("/api/health", "health", self.health, methods=["GET"])
        self.add_endpoint("/document_index/upload", "upload", protected(self.upload), methods=["POST"])
        self.add_endpoint("/document_index/delete/<file_hash>", "delete", protected(self.delete))
        self.add_endpoint(
            "/document_index/delete_source/<source_type>",
            "delete_source",
            protected(self.delete_source),
        )
        self.add_endpoint("/document_index/upload_url", "upload_url", protected(self.upload_url), methods=["POST"])
        self.add_endpoint("/document_index/add_git_repo", "add_git_repo", protected(self.add_git_repo), methods=["POST"])
        self.add_endpoint("/document_index/remove_git_repo", "remove_git_repo", protected(self.remove_git_repo), methods=["POST"])
        self.add_endpoint("/document_index/add_jira_project", "add_jira_project", protected(self.add_jira_project), methods=["POST"])
        self.add_endpoint("/document_index/update_schedule", "update_schedule", protected(self.update_schedule), methods=["POST"])
        self.add_endpoint("/document_index/load_document/<path:file_hash>", "load_document", protected(self.load_document))
        # API endpoints for remote catalog access
        self.add_endpoint("/api/catalog/search", "api_catalog_search", protected(self.api_catalog_search), methods=["GET"])
        self.add_endpoint("/api/catalog/document/<path:resource_hash>", "api_catalog_document", protected(self.api_catalog_document), methods=["GET"])
        self.add_endpoint("/api/catalog/schema", "api_catalog_schema", protected(self.api_catalog_schema), methods=["GET"])
        if self.auth_enabled:
            self.add_endpoint("/login", "login", self.login, methods=["GET", "POST"])
            self.add_endpoint("/logout", "logout", self.logout)

    def add_endpoint(self, endpoint, endpoint_name, handler, methods=None):
        self.app.add_url_rule(endpoint, endpoint_name, handler, methods=methods or ["GET"])

    def run(self, **kwargs):
        self.app.run(**kwargs)

    def require_admin(self, handler):
        @wraps(handler)
        def wrapped(*args, **kwargs):
            if not self.auth_enabled:
                return handler(*args, **kwargs)
            if session.get("admin_logged_in"):
                return handler(*args, **kwargs)
            # Allow service-to-service calls authenticated via API token
            if self.api_token:
                auth_header = request.headers.get("Authorization", "")
                if auth_header == f"Bearer {self.api_token}":
                    return handler(*args, **kwargs)
            return redirect(url_for("login"))

        return wrapped

    def _is_admin_user(self, username: str) -> bool:
        if not username:
            return False
        normalized = username.strip().lower()
        if self.default_admin_user and normalized == self.default_admin_user.strip().lower():
            return True
        if not self.admin_users:
            return True
        return normalized in self.admin_users

    def login(self):
        if not self.auth_enabled:
            return redirect(url_for("document_index"))
        if session.get("admin_logged_in"):
            return redirect(url_for("document_index"))
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            if username and password and self._is_admin_user(username):
                is_default_admin = (
                    self.default_admin_password
                    and username == self.default_admin_user
                    and password == self.default_admin_password
                )
                if is_default_admin or check_credentials(username, password, self.salt, self.accounts_path):
                    session["admin_logged_in"] = True
                    session["admin_user"] = username
                    return redirect(url_for("document_index"))
            flash("Invalid credentials")
        return render_template("login.html", sso_enabled=False, basic_auth_enabled=True)

    def logout(self):
        session.pop("admin_logged_in", None)
        session.pop("admin_user", None)
        return redirect(url_for("login"))

    def health(self):
        return jsonify({"status": "OK"}), 200

    def add_git_repo(self):
        repo_url = request.form.get("repo_url") or ""
        if not repo_url.strip():
            return jsonify({"error": "missing_repo_url"}), 400

        try:
            self.scraper_manager.collect_git(self.persistence, [repo_url.strip()])
            self._update_source_status("git", state="idle", last_run=self._now_iso())
            self._notify_update()
            return jsonify({"status": "ok"})
        except Exception as exc:
            logger.error("Failed to add git repo %s: %s", repo_url, exc)
            return jsonify({"error": "ingest_failed", "detail": str(exc)}), 500

    def remove_git_repo(self):
        repo_value = request.form.get("repo") or request.form.get("repo_url") or request.form.get("repo_name") or ""
        repo_name = self._extract_git_repo_name(repo_value)
        if not repo_name:
            return jsonify({"error": "missing_repo_name"}), 400

        try:
            self.catalog.refresh()
            to_remove = []
            for resource_hash in self.catalog.metadata_index.keys():
                metadata = self.catalog.get_metadata_for_hash(resource_hash) or {}
                if metadata.get("source_type") != "git":
                    continue
                if metadata.get("parent") == repo_name:
                    to_remove.append(resource_hash)

            if not to_remove:
                return jsonify({"error": "repo_not_found", "repo": repo_name, "deleted": 0}), 404

            for resource_hash in to_remove:
                self.persistence.delete_resource(resource_hash, flush=False)
            self.persistence.flush_index()
            self._update_source_status("git", state="idle", last_run=self._now_iso())
            self._notify_update()
            return jsonify({"status": "ok", "repo": repo_name, "deleted": len(to_remove)})
        except Exception as exc:
            logger.error("Failed to remove git repo %s: %s", repo_name, exc)
            return jsonify({"error": "delete_failed", "detail": str(exc)}), 500

    def add_jira_project(self):
        project_key = request.form.get("project_key") or ""
        if not project_key.strip():
            return jsonify({"error": "missing_project_key"}), 400

        if not self.ticket_manager or not self.ticket_manager.jira_client:
            return jsonify({"error": "jira_not_configured"}), 400

        try:
            self.ticket_manager.collect_jira(self.persistence, [project_key.strip()])
            self.persistence.flush_index()
            self._update_source_status("jira", state="idle", last_run=self._now_iso())
            self._notify_update()
            return jsonify({"status": "ok"})
        except Exception as exc:
            logger.error("Failed to add JIRA project %s: %s", project_key, exc)
            return jsonify({"error": "ingest_failed", "detail": str(exc)}), 500

    def upload(self):
        """Handle file uploads from the UI and persist them via the local files manager."""
        upload = request.files.get("file")
        if not upload:
            return jsonify({"error": "missing_file"}), 400

        filename = upload.filename or ""
        if not filename.strip():
            return jsonify({"error": "empty_filename"}), 400

        accepted = [ext.lower() for ext in self.global_config.get("ACCEPTED_FILES", [])]
        file_extension = os.path.splitext(filename)[1].lower()
        if accepted and file_extension not in accepted:
            return jsonify({"error": "unsupported_extension", "allowed": accepted}), 400

        try:
            stored_path = self.localfile_manager.ingest_uploaded_file(upload, self.persistence)
            self.persistence.flush_index()
            self._update_source_status("local_files", state="idle", last_run=self._now_iso())
            self._notify_update()
            return jsonify({"status": "ok", "path": str(stored_path)})
        except Exception as exc:
            logger.error("Failed to ingest uploaded file %s: %s", filename, exc)
            return jsonify({"error": "upload_failed", "detail": str(exc)}), 500

    def delete(self, file_hash):
        self.persistence.delete_resource(file_hash)
        self._notify_update()
        return redirect(url_for("document_index"))

    def delete_source(self, source_type):
        self.persistence.delete_by_metadata_filter("source_type", source_type)
        self._notify_update()
        return redirect(url_for("document_index"))

    def upload_url(self):
        """
        Use the ScraperManager to collect and persist a single URL provided via form data.
        """
        url = request.form.get("url")
        depth_raw = request.form.get("depth")
        depth: Optional[int] = None
        if depth_raw not in (None, ""):
            try:
                depth = int(depth_raw)
            except (TypeError, ValueError):
                return jsonify({"error": "invalid_depth"}), 400
            if depth < 0:
                return jsonify({"error": "invalid_depth"}), 400
            # LinkScraper currently uses max_depth >= 1 for the initial URL fetch.
            if depth == 0:
                depth = 1
        if url:
            logger.info("Uploading the following URL: %s", url)
            try:
                scraped_count = self.scraper_manager.collect_links(self.persistence, link_urls=[url], max_depth=depth)
                self.persistence.flush_index()
                self._update_source_status("web", state="idle", last_run=self._now_iso())
                added_to_urls = True
            except Exception as exc:
                logger.exception("Failed to upload URL: %s", exc)
                added_to_urls = False
                upload_error = str(exc)

            if added_to_urls:
                logger.info("URL uploaded successfully")
                self._notify_update()
                return jsonify({"status": "ok", "resources_scraped": scraped_count})
            else:
                return jsonify({"error": "upload_failed", "detail": upload_error}), 500
        else:
            return jsonify({"error": "missing_url"}), 400

    def update_schedule(self):
        source = (request.form.get("source") or "").strip().lower()
        schedule = (request.form.get("schedule") or "").strip()
        if not source:
            return jsonify({"error": "missing_source"}), 400

        sources_cfg = (self.config.get("data_manager", {}) or {}).get("sources", {}) or {}
        if source not in sources_cfg:
            return jsonify({"error": "unknown_source", "source": source}), 404

        if schedule:
            try:
                from croniter import croniter
                logger.debug(f"Updating source {source} schedule to {schedule}")

                croniter(schedule)
            except Exception as exc:
                return jsonify({"error": "invalid_schedule", "detail": str(exc)}), 400

        try:
            self._update_source_status(source, schedule=schedule)
            return jsonify({"status": "ok", "source": source, "schedule": schedule})
        except Exception as exc:
            logger.error("Failed to update schedule for %s: %s", source, exc)
            return jsonify({"error": "schedule_update_failed", "detail": str(exc)}), 500

    def load_document(self, file_hash):
        index = self.catalog.file_index
        if file_hash in index.keys():
            path = self.catalog.get_filepath_for_hash(file_hash)
            metadata = self.catalog.get_metadata_for_hash(file_hash) or {}

            document = ""
            suffix = metadata.get("suffix") or (path.suffix if path else "")

            try:
                if suffix.lower() in {".html", ".htm"} and path and path.exists():
                    # For HTML, return the raw document so the preview can render fully.
                    document = path.read_text(encoding="utf-8", errors="ignore")
                elif suffix.lower() == ".pdf" and path and path.exists():
                    document = f"__PDF_INLINE__::{path.as_posix()}"
                else:
                    document_obj = self.catalog.get_document_for_hash(file_hash)
                    if hasattr(document_obj, "page_content"):
                        document = document_obj.page_content or ""
                    elif isinstance(document_obj, str):
                        document = document_obj
                    else:
                        document = load_text_from_path(path) if path else ""
            except Exception as exc:
                logger.warning("Failed to load document content for %s: %s", file_hash, exc)

            display_name = metadata.get("display_name") or metadata.get("file_name") or ""
            title = metadata.get("title") or display_name
            return jsonify(
                {
                    "document": document or "",
                    "display_name": display_name,
                    "source_type": metadata.get("source_type") or "",
                    "original_url": metadata.get("url") or "",
                    "title": title or "",
                }
            )

        return jsonify(
            {
                "document": "Document not found",
                "display_name": "Error",
                "source_type": "null",
                "original_url": "no_url",
                "title": "Not found",
            }
        )

    def _notify_update(self) -> None:
        if not self.post_update_hook:
            return
        try:
            self.post_update_hook()
        except Exception as exc:
            logger.warning("Post-update hook failed: %s", exc)

    def _load_source_status(self) -> Dict[str, Dict[str, str]]:
        if not self.status_file.exists():
            return {}
        try:
            import json

            return json.loads(self.status_file.read_text())
        except Exception as exc:
            logger.warning("Failed to read source status file: %s", exc)
            return {}

    def _update_source_status(
        self,
        source: str,
        *,
        state: Optional[str] = None,
        last_run: Optional[str] = None,
        schedule: Optional[str] = None,
    ) -> None:
        try:
            import json
            data = self._load_source_status()
            entry = data.get(source, {})
            if state is not None:
                entry["state"] = state
            if last_run is not None:
                entry["last_run"] = last_run
            if schedule is not None:
                if schedule:
                    entry["schedule"] = schedule
                else:
                    entry.pop("schedule", None)
            data[source] = entry
            logger.debug(f"Updated source status with state {state}, last_run: {last_run}, schedule: {schedule}")
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            self.status_file.write_text(json.dumps(data))
        except Exception as exc:
            logger.warning("Failed to update source status: %s", exc)

    def _now_iso(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    def _extract_git_repo_name(self, value: str) -> str:
        if not value:
            return ""
        raw = value.strip()
        if not raw:
            return ""
        pattern = r"(?:github|gitlab)\.[\w.]+[:/][^/]+/([\w.-]+)(?:\.git|/|$)"
        match = re.search(pattern, raw, re.IGNORECASE)
        if match:
            return match.group(1)
        candidate = raw.rstrip("/").split("/")[-1]
        if candidate.endswith(".git"):
            candidate = candidate[:-4]
        return candidate

    # -------------------------
    # API endpoints
    # -------------------------
    def api_catalog_search(self):
        start_time = time.monotonic()
        logger.debug("[catalog_search] BEGIN args=%s", dict(request.args))
        query = request.args.get("q") or request.args.get("query") or ""
        if not query.strip():
            return jsonify({"hits": [], "total_duration": 0.0})
        limit = request.args.get("limit", default=5, type=int)
        window = request.args.get("window", default=-1, type=int)
        search_content = request.args.get("search_content", default="true").lower() != "false"
        mode = (request.args.get("mode") or "").strip().lower()
        regex = _parse_bool(request.args.get("regex"), default=False)
        case_sensitive = _parse_bool(request.args.get("case_sensitive"), default=False)
        max_matches_per_file = request.args.get("max_matches_per_file", default=3, type=int)
        before = request.args.get("before", default=0, type=int)
        after = request.args.get("after", default=0, type=int)

        filters, free_query = _parse_metadata_query(query)
        q_lower = free_query.lower()
        hits = []
        _t = time.monotonic()
        self.catalog.refresh()
        logger.debug("[catalog_search] after refresh: %.3fs", time.monotonic() - _t)
        if not search_content:
            results = self.catalog.search_metadata(free_query, limit=limit, filters=filters)
            for item in results:
                metadata = item.get("metadata") or {}
                snippet = (
                    metadata.get("display_name")
                    or metadata.get("file_name")
                    or metadata.get("title")
                    or metadata.get("url")
                    or ""
                )
                hits.append(
                    {
                        "hash": item["hash"],
                        "path": str(item["path"]),
                        "metadata": metadata,
                        "snippet": snippet,
                    }
                )
        else:
            if mode == "grep":
                if not free_query.strip():
                    return jsonify({"hits": [], "total_duration": 0.0})

                candidate_hashes = None
                candidate_metadata: Dict[str, Dict[str, object]] = {}
                if filters:
                    candidates = self.catalog.search_metadata("", limit=None, filters=filters)
                    candidate_hashes = {item["hash"] for item in candidates}
                    candidate_metadata = {
                        item["hash"]: item.get("metadata") or {}
                        for item in candidates
                    }

                if _ripgrep_available():
                    logger.debug("[catalog_search] entered ripgrep branch (+%.3fs)", time.monotonic() - start_time)
                    # When filters narrowed the candidates, pass the file
                    # paths directly. Otherwise let ripgrep walk the corpus
                    # root and we look the hash up by path afterwards.
                    candidate_paths: Optional[List[Path]] = None
                    if candidate_hashes is not None:
                        candidate_paths = []
                        for resource_hash in candidate_hashes:
                            p = self.catalog.get_filepath_for_hash(resource_hash)
                            if p:
                                candidate_paths.append(Path(p))

                    try:
                        _t = time.monotonic()
                        if candidate_paths is not None and 0 < len(candidate_paths) <= 1000:
                            rg_hits = _run_ripgrep(
                                free_query,
                                roots=candidate_paths,
                                regex=regex,
                                case_sensitive=case_sensitive,
                                before=before,
                                after=after,
                                max_matches_per_file=max_matches_per_file,
                                limit=limit,
                            )
                        else:
                            root = getattr(self.catalog, "data_path", None)
                            if root is None:
                                rg_hits = []
                            else:
                                rg_hits = _run_ripgrep(
                                    free_query,
                                    roots=[Path(root)],
                                    regex=regex,
                                    case_sensitive=case_sensitive,
                                    before=before,
                                    after=after,
                                    max_matches_per_file=max_matches_per_file,
                                    # Pull a few extra in case some hit paths
                                    # aren't in the catalog (raw FS entries).
                                    limit=max(limit * 4, limit),
                                )
                        logger.debug("[catalog_search] _run_ripgrep took %.3fs, %d hits", time.monotonic() - _t, len(rg_hits))
                    except re.error as exc:
                        return jsonify({"error": f"invalid_regex: {exc}"}), 400

                    # Look hash up by path. The catalog stores relative
                    # file_path values; build a string → hash dict from the
                    # in-memory _file_index without any filesystem calls.
                    _t = time.monotonic()
                    file_index = getattr(self.catalog, "_file_index", {}) or {}
                    data_root = str(getattr(self.catalog, "data_path", "") or "").rstrip("/")
                    hash_by_relpath: Dict[str, str] = {str(p): h for h, p in file_index.items()}
                    logger.debug("[catalog_search] built hash_by_relpath (%d entries) in %.3fs", len(hash_by_relpath), time.monotonic() - _t)

                    for rec in rg_hits:
                        p_str = str(rec["path"])
                        rel = p_str
                        if data_root and p_str.startswith(data_root + "/"):
                            rel = p_str[len(data_root) + 1:]
                        resource_hash = (
                            hash_by_relpath.get(rel)
                            or hash_by_relpath.get(p_str)
                        )
                        if not resource_hash:
                            continue
                        metadata = (
                            candidate_metadata.get(resource_hash)
                            or self.catalog.get_metadata_for_hash(resource_hash)
                            or {}
                        )
                        matches = rec["matches"]
                        hits.append(
                            {
                                "hash": resource_hash,
                                "path": p_str,
                                "metadata": metadata,
                                "matches": matches,
                                "snippet": matches[0].get("text", "") if matches else "",
                            }
                        )
                        if len(hits) >= limit:
                            break
                else:
                    # Fallback: original Python regex scan.
                    try:
                        pattern = _compile_query_pattern(
                            free_query, regex=regex, case_sensitive=case_sensitive
                        )
                    except re.error as exc:
                        return jsonify({"error": f"invalid_regex: {exc}"}), 400

                    if candidate_hashes is None:
                        iterable = list(self.catalog.iter_files())
                    else:
                        iterable = []
                        for resource_hash in candidate_hashes:
                            path = self.catalog.get_filepath_for_hash(resource_hash)
                            if path:
                                iterable.append((resource_hash, path))

                    for resource_hash, path in iterable:
                        metadata = candidate_metadata.get(resource_hash) or self.catalog.get_metadata_for_hash(resource_hash) or {}
                        text = load_text_from_path(path) or ""
                        if not text:
                            continue
                        matches = _grep_text_lines(
                            text,
                            pattern,
                            before=before,
                            after=after,
                            max_matches=max_matches_per_file,
                        )
                        if not matches:
                            continue
                        hits.append(
                            {
                                "hash": resource_hash,
                                "path": str(path),
                                "metadata": metadata,
                                "matches": matches,
                                "snippet": matches[0].get("text", ""),
                            }
                        )
                        if len(hits) >= limit:
                            break

                total_duration = time.monotonic() - start_time
                logger.warning(
                    "[catalog_search] DONE in %.3fs with %d hits",
                    total_duration,
                    len(hits),
                )
                return jsonify({"hits": hits, "total_duration": total_duration})

            candidate_hashes = None
            candidate_metadata: Dict[str, Dict[str, object]] = {}
            if filters:
                candidates = self.catalog.search_metadata("", limit=None, filters=filters)
                candidate_hashes = {item["hash"] for item in candidates}
                candidate_metadata = {
                    item["hash"]: item.get("metadata") or {}
                    for item in candidates
                }

            if candidate_hashes is None:
                iterable = list(self.catalog.iter_files())
            else:
                iterable = []
                for resource_hash in candidate_hashes:
                    path = self.catalog.get_filepath_for_hash(resource_hash)
                    if path:
                        iterable.append((resource_hash, path))

            for resource_hash, path in iterable:
                metadata = candidate_metadata.get(resource_hash) or self.catalog.get_metadata_for_hash(resource_hash) or {}
                flattened_meta = _flatten_metadata(metadata)
                if q_lower:
                    meta_match = any(q_lower in k.lower() or q_lower in v.lower() for k, v in flattened_meta.items())
                else:
                    meta_match = True

                snippet = ""
                content_match = False
                text = ""
                if q_lower:
                    text = load_text_from_path(path) or ""
                    if text:
                        idx = text.lower().find(q_lower)
                        if idx != -1:
                            content_match = True
                            snippet = _collect_snippet(text, idx, len(free_query), window=window)
                    else:
                        logger.error("No text content loaded from %s for search", path)

                if meta_match and not content_match:
                    if q_lower:
                        if not text:
                            text = load_text_from_path(path) or ""
                            if not text:
                                logger.error("No text content loaded from %s for metadata match", path)
                        snippet = text
                    else:
                        snippet = (
                            metadata.get("display_name")
                            or metadata.get("file_name")
                            or metadata.get("url")
                            or ""
                        )

                if meta_match or content_match:
                    hits.append(
                        {
                            "hash": resource_hash,
                            "path": str(path),
                            "metadata": metadata,
                            "snippet": snippet,
                        }
                    )
                if len(hits) >= limit:
                    break

        total_duration = time.monotonic() - start_time
        logger.debug("Catalog search completed in %.3f seconds with %d hits", total_duration, len(hits))
        return jsonify({"hits": hits, "total_duration": total_duration})

    def api_catalog_document(self, resource_hash: str):
        max_chars = request.args.get("max_chars", default=4000, type=int)
        self.catalog.refresh()
        path = self.catalog.get_filepath_for_hash(resource_hash)
        if not path:
            return jsonify({"error": "not_found"}), 404
        metadata = self.catalog.get_metadata_for_hash(resource_hash) or {}
        # Cache the full document body; per-request truncation happens below.
        text = _cached_document_text(str(path))
        if max_chars and len(text) > max_chars:
            text = text[:max_chars]
        return jsonify({"hash": resource_hash, "path": str(path), "metadata": metadata, "text": text})

    def api_catalog_schema(self):
        """
        Return metadata schema hints for agents: supported keys and distinct values for source_type/suffix.
        """
        keys = sorted(_METADATA_COLUMN_MAP.keys())
        distinct = self.catalog.get_distinct_metadata(["source_type", "suffix"])
        return jsonify({
            "keys": keys,
            "source_types": distinct.get("source_type", []),
            "suffixes": distinct.get("suffix", []),
        })


def _flatten_metadata(data: Dict[str, object], prefix: str = "") -> Dict[str, str]:
    flattened: Dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten_metadata(value, prefix=full_key))
        else:
            flattened[full_key] = "" if value is None else str(value)
    return flattened


_METADATA_ALIAS_MAP = {
    "resource_type": "source_type",
    "resource_id": "ticket_id",
}


def _parse_metadata_query(query: str) -> Tuple[Dict[str, str] | List[Dict[str, str]], str]:
    filters_groups: List[Dict[str, str]] = []
    current_group: Dict[str, str] = {}
    free_tokens = []
    for token in shlex.split(query):
        if token.upper() == "OR":
            if current_group:
                filters_groups.append(current_group)
                current_group = {}
            continue
        if ":" in token:
            key, value = token.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key and value:
                # Normalize legacy keys to canonical column names
                key = _METADATA_ALIAS_MAP.get(key, key)
                current_group[key] = value
                continue
        free_tokens.append(token)

    if current_group:
        filters_groups.append(current_group)

    if not filters_groups:
        filters: Dict[str, str] | List[Dict[str, str]] = {}
    elif len(filters_groups) == 1:
        filters = filters_groups[0]
    else:
        filters = filters_groups

    return filters, " ".join(free_tokens)


def _parse_bool(value: Optional[str], *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _compile_query_pattern(query: str, *, regex: bool, case_sensitive: bool) -> re.Pattern[str]:
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = query if regex else re.escape(query)
    return re.compile(pattern, flags)


def _grep_text_lines(
    text: str,
    pattern: re.Pattern[str],
    *,
    before: int = 0,
    after: int = 0,
    max_matches: int = 3,
) -> list[Dict[str, object]]:
    if max_matches <= 0:
        return []
    lines = text.splitlines()
    matches: list[Dict[str, object]] = []
    for idx, line in enumerate(lines):
        if not pattern.search(line):
            continue
        match = {
            "line": idx + 1,
            "text": line,
            "before": lines[max(0, idx - before) : idx] if before else [],
            "after": lines[idx + 1 : idx + 1 + after] if after else [],
        }
        matches.append(match)
        if len(matches) >= max_matches:
            break
    return matches


def _collect_snippet(text: str, start_idx: int, query_len: int, window: int = -1) -> str:
    start = max(start_idx - window, 0) if window >= 0 else 0
    end = min(start_idx + query_len + window, len(text)) if window >= 0 else len(text)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    excerpt = text[start:end].replace("\n", " ")
    return f"{prefix}{excerpt}{suffix}"


# --- ripgrep integration ----------------------------------------------------

_RG_MISSING_WARNED = False


def _ripgrep_available() -> bool:
    """Return True iff `rg` is on PATH; warn once if not."""
    global _RG_MISSING_WARNED
    if shutil.which("rg") is not None:
        return True
    if not _RG_MISSING_WARNED:
        logger.warning(
            "ripgrep (`rg`) not found on PATH; catalog grep endpoint will "
            "fall back to the legacy Python regex scan. Install ripgrep to "
            "speed up corpus search 10–100×."
        )
        _RG_MISSING_WARNED = True
    return False


def _rg_record_text(rec: Dict[str, object]) -> str:
    """Extract the text from a ripgrep --json record (handles bytes form)."""
    if not isinstance(rec, dict):
        return ""
    if "text" in rec:
        return str(rec.get("text") or "")
    if "bytes" in rec:
        try:
            import base64
            return base64.b64decode(str(rec.get("bytes"))).decode("utf-8", errors="replace")
        except Exception:
            return ""
    return ""


# Per-match-line truncation (chars). Caps a single matching line so that
# files with very long single-line content (e.g. Jira ticket Description
# fields stored without newlines) cannot blow up the LLM context.
RG_MAX_LINE_CHARS = 500

# Total response budget across all matches in a single grep call. Once
# accumulated match/context text exceeds this, we stop adding more matches.
# Keeps a worst-case grep call well under the LLM context window.
RG_MAX_RESPONSE_CHARS = 40_000

_TRUNC_SUFFIX = "...[truncated]"


def _clip_line(text: str, *, limit: int = RG_MAX_LINE_CHARS) -> str:
    if not text:
        return text
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(_TRUNC_SUFFIX))] + _TRUNC_SUFFIX


def _run_ripgrep(
    pattern: str,
    *,
    roots: List[Path],
    regex: bool,
    case_sensitive: bool,
    before: int,
    after: int,
    max_matches_per_file: int,
    limit: int,
    timeout_s: float = 30.0,
) -> List[Dict[str, object]]:
    """Invoke `rg --json` and return a list of file-grouped hit dicts:
    [{"path": str, "matches": [{"line": int, "text": str,
                                 "before": [str], "after": [str]}]}, ...].

    Output is bounded by two server-side guards:
      * `--max-columns 500 --max-columns-preview` truncates any single ripgrep
        line at 500 chars (matters for files with very long single lines —
        Jira Description fields can be 80KB+ on one line, which would
        otherwise dominate the response).
      * Total accumulated response text is capped at RG_MAX_RESPONSE_CHARS;
        further hits past that point are dropped (caller can see this via
        the file count returned).

    Stops after `limit` distinct files. Caller is responsible for resolving
    path → resource_hash and metadata.
    """
    if not roots:
        return []
    cmd: List[str] = [
        "rg",
        "--json",
        "--no-config",
        "--no-ignore",
        "--hidden",
        "--max-columns", str(RG_MAX_LINE_CHARS),
        "--max-columns-preview",
        "-e", pattern,
        "--max-count", str(max(1, max_matches_per_file)),
    ]
    if not case_sensitive:
        cmd.append("-i")
    if not regex:
        cmd.append("-F")
    if before > 0:
        cmd.extend(["--before-context", str(before)])
    if after > 0:
        cmd.extend(["--after-context", str(after)])
    for root in roots:
        cmd.append(str(root))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("ripgrep timed out after %.1fs for pattern %r", timeout_s, pattern)
        return []
    if proc.returncode == 2:
        # Invalid regex (or other rg error). Surface via exception to allow
        # caller to return HTTP 400.
        raise re.error(proc.stderr.strip() or "ripgrep invalid pattern")

    # Parse the JSONL stream. Group `match` and `context` events under their
    # file's `begin` path. ripgrep emits `begin` once per file, then a
    # sequence of `match`/`context` events, then `end`.
    by_path: Dict[str, Dict[str, object]] = {}
    cur_path: Optional[str] = None
    order: List[str] = []
    # We keep a per-file rolling buffer of "pending before-context lines"
    # so we can attach context to the matches.
    pending_before: Dict[str, List[str]] = {}
    last_match_by_path: Dict[str, Dict[str, object]] = {}
    after_remaining: Dict[str, int] = {}
    # Running budget — once accumulated text exceeds the cap, drop further
    # match/context events but keep parsing JSON cheaply.
    accumulated_chars = 0
    budget_exhausted = False

    for raw in proc.stdout.splitlines():
        if not raw:
            continue
        try:
            evt = _json.loads(raw)
        except Exception:
            continue
        etype = evt.get("type")
        data = evt.get("data") or {}
        if etype == "begin":
            cur_path = (data.get("path") or {}).get("text") or ""
            if cur_path and cur_path not in by_path:
                by_path[cur_path] = {"path": cur_path, "matches": []}
                order.append(cur_path)
                pending_before[cur_path] = []
                after_remaining[cur_path] = 0
        elif etype == "match" and cur_path and not budget_exhausted:
            raw_text = _rg_record_text((data.get("lines") or {})).rstrip("\n")
            text = _clip_line(raw_text)
            line_no = data.get("line_number")
            match_entry = {
                "line": int(line_no) if isinstance(line_no, int) else 0,
                "text": text,
                "before": [_clip_line(b) for b in pending_before.get(cur_path, [])[-before:]] if before else [],
                "after": [],
            }
            by_path[cur_path]["matches"].append(match_entry)
            last_match_by_path[cur_path] = match_entry
            after_remaining[cur_path] = after
            pending_before[cur_path] = []
            accumulated_chars += len(text) + sum(len(b) for b in match_entry["before"])
            if accumulated_chars >= RG_MAX_RESPONSE_CHARS:
                budget_exhausted = True
        elif etype == "context" and cur_path and not budget_exhausted:
            raw_text = _rg_record_text((data.get("lines") or {})).rstrip("\n")
            text = _clip_line(raw_text)
            remaining = after_remaining.get(cur_path, 0)
            if remaining > 0 and last_match_by_path.get(cur_path) is not None:
                last_match_by_path[cur_path]["after"].append(text)
                after_remaining[cur_path] = remaining - 1
                accumulated_chars += len(text)
                if accumulated_chars >= RG_MAX_RESPONSE_CHARS:
                    budget_exhausted = True
            else:
                pending_before.setdefault(cur_path, []).append(text)
        elif etype == "end":
            cur_path = None

    if budget_exhausted:
        logger.debug("[catalog_search] grep response budget exhausted (%d chars) for pattern %r",
                     accumulated_chars, pattern)

    # Cap to `limit` files in the order ripgrep returned them.
    out: List[Dict[str, object]] = []
    for p in order:
        rec = by_path[p]
        if rec["matches"]:
            out.append(rec)
        if len(out) >= max(1, limit):
            break
    return out


# --- Document-fetch LRU cache ----------------------------------------------

_DOC_CACHE_SIZE = int(os.environ.get("CATALOG_DOC_CACHE_SIZE", "256"))


@lru_cache(maxsize=_DOC_CACHE_SIZE)
def _cached_document_text(path_str: str) -> str:
    """Cache the *full* document text. Per-request truncation happens above."""
    return load_text_from_path(Path(path_str)) or ""
