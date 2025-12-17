from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional, Dict
import secrets

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from flask_cors import CORS

from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager
from src.data_manager.collectors.utils.index_utils import CatalogService
from src.data_manager.vectorstore.loader_utils import load_text_from_path
from src.interfaces.chat_app.document_utils import add_uploaded_file
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
    ) -> None:
        self.app = app
        self.config = load_config()
        self.global_config = self.config["global"]

        self.data_path = self.global_config["DATA_PATH"]
        self.persistence = PersistenceService(self.data_path)
        self.catalog = CatalogService(self.data_path)

        secret_key = read_secret("FLASK_UPLOADER_APP_SECRET_KEY") or secrets.token_hex(32)
        self.app.secret_key = secret_key

        self.app.config["UPLOAD_FOLDER"] = os.path.join(self.data_path, "manual_uploads")
        self.app.config["WEBSITE_FOLDER"] = os.path.join(self.data_path, "manual_websites")

        os.makedirs(self.app.config["UPLOAD_FOLDER"], exist_ok=True)
        os.makedirs(self.app.config["WEBSITE_FOLDER"], exist_ok=True)

        self.scraper_manager = ScraperManager(dm_config=self.config.get("data_manager"))
        self.post_update_hook = post_update_hook

        CORS(self.app)

        self.add_endpoint("/", "index", self.index)
        self.add_endpoint("/api/health", "health", self.health, methods=["GET"])
        self.add_endpoint("/document_index/", "document_index", self.document_index)
        self.add_endpoint("/document_index/index", "document_index_alt", self.document_index)
        self.add_endpoint("/document_index/upload", "upload", self.upload, methods=["POST"])
        self.add_endpoint("/document_index/delete/<file_hash>", "delete", self.delete)
        self.add_endpoint(
            "/document_index/delete_source/<source_type>",
            "delete_source",
            self.delete_source,
        )
        self.add_endpoint("/document_index/upload_url", "upload_url", self.upload_url, methods=["POST"])
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
        sources_index = {}
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

            sources_index.setdefault(source_type, []).append((source_hash, title))

        return render_template("document_index.html", sources_index=sources_index.items())

    def upload(self):
        if "file" not in request.files:
            flash("No file part")
            return redirect(url_for("document_index"))

        file = request.files["file"]
        if file.filename == "":
            flash("No selected file")
            return redirect(url_for("document_index"))

        file_extension = os.path.splitext(file.filename)[1]
        if file and file_extension in self.global_config["ACCEPTED_FILES"]:
            try:
                resource = add_uploaded_file(
                    target_dir=self.app.config["UPLOAD_FOLDER"],
                    file=file,
                    file_extension=file_extension,
                )
                self.scraper_manager.register_resource(
                    target_dir=Path(self.app.config["UPLOAD_FOLDER"]), resource=resource
                )
                flash("File uploaded successfully")
                self._notify_update()
            except Exception:
                flash(
                    "File under this name already exists. If you would like to upload a new file, "
                    "please delete the old one."
                )

        return redirect(url_for("document_index"))

    def delete(self, file_hash):
        self.persistence.delete_resource(file_hash)
        self._notify_update()
        return redirect(url_for("document_index"))

    def delete_source(self, source_type):
        self.persistence.delete_by_metadata_filter("source_type", source_type)
        self._notify_update()
        return redirect(url_for("document_index"))

    def upload_url(self):
        url = request.form.get("url")
        if url:
            logger.info("Uploading the following URL: %s", url)
            try:
                target_dir = Path(self.app.config["WEBSITE_FOLDER"])
                resources = self.scraper_manager.web_scraper.scrape(url)
                for resource in resources:
                    self.scraper_manager.register_resource(target_dir, resource)
                self.scraper_manager.persist_sources()
                added_to_urls = True
            except Exception as exc:
                logger.error("Failed to upload URL: %s", exc)
                added_to_urls = False

            if added_to_urls:
                flash("URL uploaded successfully")
                self._notify_update()
            else:
                flash("Failed to add URL")
        else:
            flash("No URL provided")

        return redirect(url_for("document_index"))

    def load_document(self, file_hash):
        index = self.catalog.file_index
        if file_hash in index.keys():
            document = self.catalog.get_document_for_hash(file_hash)
            metadata = self.catalog.get_metadata_for_hash(file_hash)

            title = metadata.get("title") or metadata.get("display_name")
            return jsonify(
                {
                    "document": document,
                    "display_name": metadata.get("display_name"),
                    "source_type": metadata.get("source_type"),
                    "original_url": metadata.get("url"),
                    "title": title,
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
