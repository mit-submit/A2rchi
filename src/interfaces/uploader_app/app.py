from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Dict
import secrets

from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_cors import CORS

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.localfile_manager import LocalFileManager
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager
from src.data_manager.collectors.utils.index_utils import CatalogService
from src.data_manager.collectors.tickets.ticket_manager import TicketManager
from src.data_manager.vectorstore.loader_utils import load_text_from_path
from src.utils.config_loader import load_config
from src.utils.env import read_secret
from src.utils.logging import get_logger

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
        self.config = load_config()
        self.global_config = self.config["global"]

        self.data_path = self.global_config["DATA_PATH"]
        self.persistence = PersistenceService(self.data_path)
        self.catalog = CatalogService(self.data_path)
        self.status_file = status_file or (Path(self.data_path) / "ingestion_status.json")

        secret_key = read_secret("FLASK_UPLOADER_APP_SECRET_KEY") or secrets.token_hex(32)
        self.app.secret_key = secret_key

        self.scraper_manager = ScraperManager(dm_config=self.config.get("data_manager"))
        self.ticket_manager = TicketManager(dm_config=self.config.get("data_manager"))
        self.localfile_manager = LocalFileManager(dm_config=self.config.get("data_manager"))
        self.post_update_hook = post_update_hook

        CORS(self.app)

        self.add_endpoint("/", "index", self.index)
        self.add_endpoint("/api/health", "health", self.health, methods=["GET"])
        self.add_endpoint("/document_index/", "document_index", self.document_index)
        self.add_endpoint("/document_index/index", "document_index_alt", self.document_index)
        self.add_endpoint("/document_index/upload", "upload", self.upload, methods=["POST"])
        self.add_endpoint("/document_index/delete/<file_hash>", "delete", self.delete)
        self.add_endpoint(
            "/document_index/delete_source/<_type>",
            "delete_source",
            self.delete_source,
        )
        self.add_endpoint("/document_index/upload_url", "upload_url", self.upload_url, methods=["POST"])
        self.add_endpoint("/document_index/add_git_repo", "add_git_repo", self.add_git_repo, methods=["POST"])
        self.add_endpoint("/document_index/add_jira_project", "add_jira_project", self.add_jira_project, methods=["POST"])
        self.add_endpoint("/document_index/load_document/<path:file_hash>", "load_document", self.load_document)
        # API endpoints for remote catalog access
        self.add_endpoint("/api/catalog/search", "api_catalog_search", self.api_catalog_search, methods=["GET"])
        self.add_endpoint("/api/catalog/document/<path:resource_hash>", "api_catalog_document", self.api_catalog_document, methods=["GET"])

    def add_endpoint(self, endpoint, endpoint_name, handler, methods=None):
        self.app.add_url_rule(endpoint, endpoint_name, handler, methods=methods or ["GET"])

    def run(self, **kwargs):
        self.app.run(**kwargs)

    def health(self):
        return jsonify({"status": "OK"}, 200)

    def index(self):
        return redirect(url_for("document_index"))

    def document_index(self):
        # Ensure the catalog reflects the latest ingested resources
        try:
            self.catalog.refresh()
        except Exception as exc:
            logger.warning("Failed to refresh catalog before rendering index: %s", exc)

        # Seed all configured source types so empty sources still render a card.
        configured_sources = list((self.config.get("data_manager", {}) or {}).get("sources", {}).keys())
        sources_index = {name: [] for name in configured_sources}
        source_status = self._load_source_status()

        for source_hash in self.catalog.metadata_index.keys():
            metadata_source = self.catalog.get_metadata_for_hash(source_hash)
            if not isinstance(metadata_source, dict):
                logger.info("Metadata for hash %s missing or invalid; skipping", source_hash)
                continue

            source_type = metadata_source.get("source_type")
            if not source_type:
                logger.info("Metadata for hash %s missing source_type; skipping", source_hash)
                continue

            title = metadata_source.get("ticket_id") or metadata_source.get("url")
            if not title:
                title = metadata_source.get("display_name") or source_hash

            ts = metadata_source.get("modified_at") or metadata_source.get("created_at") or metadata_source.get("ingested_at") or ""
            sources_index.setdefault(source_type, []).append(
                {"hash": source_hash, "title": title, "ts": ts}
            )

        # sort each source list by timestamp (desc) then title
        def _sort_key(entry: dict):
            ts = entry.get("ts") or ""
            return (ts, entry.get("title") or "")

        for key, entries in sources_index.items():
            entries.sort(key=_sort_key, reverse=True)

        sorted_sources = sorted(sources_index.items(), key=lambda x: x[0])
        return render_template("document_index.html", sources_index=sorted_sources, source_status=source_status)

    def add_git_repo(self):
        repo_url = request.form.get("repo_url") or ""
        if not repo_url.strip():
            return jsonify({"error": "missing_repo_url"}), 400

        try:
            self.scraper_manager._collect_git_resources([repo_url.strip()], self.persistence)
            self.persistence.flush_index()
            self._update_source_status("git", state="idle", last_run=self._now_iso())
            self._notify_update()
            return jsonify({"status": "ok"})
        except Exception as exc:
            logger.error("Failed to add git repo %s: %s", repo_url, exc)
            return jsonify({"error": "ingest_failed", "detail": str(exc)}), 500

    def add_jira_project(self):
        project_key = request.form.get("project_key") or ""
        if not project_key.strip():
            return jsonify({"error": "missing_project_key"}), 400

        if not self.ticket_manager or not self.ticket_manager.jira_client:
            return jsonify({"error": "jira_not_configured"}), 400

        try:
            self.ticket_manager.collect_jira_project(self.persistence, project_key.strip())
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
        if url:
            logger.info("Uploading the following URL: %s", url)
            try:
                self.scraper_manager.collect_links(self.persistence, link_urls=[url])
                self.persistence.flush_index()
                self._update_source_status("links", state="idle", last_run=self._now_iso())
                added_to_urls = True
            except Exception as exc:
                logger.exception("Failed to upload URL: %s", exc)
                added_to_urls = False

            if added_to_urls:
                logger.info("URL uploaded successfully")
                self._notify_update()
                return jsonify({"status": "ok"})
            else:
                return jsonify({"error": "upload_failed", "detail": str(exc)}), 500
        else:
            return jsonify({"error": "missing_url"}), 400

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

            title = metadata.get("title") or metadata.get("display_name")
            return jsonify(
                {
                    "document": document or "",
                    "display_name": metadata.get("display_name") or "",
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

    def _update_source_status(self, source: str, *, state: Optional[str] = None, last_run: Optional[str] = None) -> None:
        try:
            import json
            data = self._load_source_status()
            entry = data.get(source, {})
            if state is not None:
                entry["state"] = state
            if last_run is not None:
                entry["last_run"] = last_run
            data[source] = entry
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            self.status_file.write_text(json.dumps(data))
        except Exception as exc:
            logger.warning("Failed to update source status: %s", exc)

    def _now_iso(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    # -------------------------
    # API endpoints
    # -------------------------
    def api_catalog_search(self):
        query = request.args.get("q") or request.args.get("query") or ""
        if not query.strip():
            return jsonify({"hits": []})
        limit = request.args.get("limit", default=5, type=int)
        search_content = request.args.get("search_content", default="true").lower() != "false"

        q_lower = query.lower()
        hits = []
        self.catalog.refresh()
        for resource_hash, path in self.catalog.iter_files():
            metadata = self.catalog.get_metadata_for_hash(resource_hash) or {}
            flattened_meta = _flatten_metadata(metadata)
            meta_match = any(q_lower in k.lower() or q_lower in v.lower() for k, v in flattened_meta.items())

            snippet = ""
            content_match = False
            if search_content:
                text = load_text_from_path(path) or ""
                if text:
                    idx = text.lower().find(q_lower)
                    if idx != -1:
                        content_match = True
                        snippet = _collect_snippet(text, idx, len(query))

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

        return jsonify({"hits": hits})

    def api_catalog_document(self, resource_hash: str):
        max_chars = request.args.get("max_chars", default=4000, type=int)
        self.catalog.refresh()
        path = self.catalog.get_filepath_for_hash(resource_hash)
        if not path:
            return jsonify({"error": "not_found"}), 404
        metadata = self.catalog.get_metadata_for_hash(resource_hash) or {}
        text = load_text_from_path(path) or ""
        if max_chars and len(text) > max_chars:
            text = text[:max_chars]
        return jsonify({"hash": resource_hash, "path": str(path), "metadata": metadata, "text": text})


def _flatten_metadata(data: Dict[str, object], prefix: str = "") -> Dict[str, str]:
    flattened: Dict[str, str] = {}
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flattened.update(_flatten_metadata(value, prefix=full_key))
        else:
            flattened[full_key] = "" if value is None else str(value)
    return flattened


def _collect_snippet(text: str, start_idx: int, query_len: int, window: int = 240) -> str:
    start = max(start_idx - window, 0)
    end = min(start_idx + query_len + window, len(text))
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    excerpt = text[start:end].replace("\n", " ")
    return f"{prefix}{excerpt}{suffix}"
