from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from langchain_core.documents import Document

from src.utils.logging import get_logger

logger = get_logger(__name__)


class ChunkCache:
    """Persist and reload chunked documents keyed by resource hash."""

    MANIFEST_FILENAME = "manifest.json"

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._chunks_dir = self.cache_dir / "chunks"
        self._chunks_dir.mkdir(parents=True, exist_ok=True)

        self._manifest_path = self.cache_dir / self.MANIFEST_FILENAME
        self._manifest: Dict[str, Dict[str, object]] = self._load_manifest()

    def _load_manifest(self) -> Dict[str, Dict[str, object]]:
        if not self._manifest_path.exists():
            return {}

        try:
            with self._manifest_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read chunk cache manifest %s: %s. Resetting.", self._manifest_path, exc)
            return {}

        if not isinstance(data, dict):
            logger.warning("Invalid manifest structure at %s; resetting.", self._manifest_path)
            return {}

        entries = data.get("entries")
        if isinstance(entries, dict):
            sanitized: Dict[str, Dict[str, object]] = {}
            for resource_hash, payload in entries.items():
                if isinstance(resource_hash, str) and isinstance(payload, dict):
                    sanitized[resource_hash] = payload
                else:
                    logger.debug("Skipping malformed manifest entry for %r", resource_hash)
            return sanitized

        logger.debug("Manifest at %s missing 'entries'; starting with empty cache.", self._manifest_path)
        return {}

    def _write_manifest(self) -> None:
        payload = {
            "version": 1,
            "entries": self._manifest,
        }
        with self._manifest_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)

    def list_hashes(self) -> Sequence[str]:
        return tuple(self._manifest.keys())

    def contains(self, resource_hash: str) -> bool:
        return resource_hash in self._manifest

    def chunk_path(self, resource_hash: str) -> Path:
        return self._chunks_dir / f"{resource_hash}.json"

    def upsert(self, resource_hash: str, chunks: List[str], metadatas: List[Dict], *, filename: Optional[str] = None) -> None:
        if not chunks:
            logger.debug("Skipping chunk cache upsert for %s due to empty chunk list.", resource_hash)
            return

        if len(chunks) != len(metadatas):
            raise ValueError(
                f"Chunk and metadata length mismatch for {resource_hash}: {len(chunks)} vs {len(metadatas)}"
            )

        records: List[Dict[str, object]] = []
        for chunk, metadata in zip(chunks, metadatas):
            if not isinstance(metadata, dict):
                raise ValueError(f"Chunk metadata must be a dict; got {type(metadata)!r}")
            records.append({"page_content": chunk, "metadata": metadata})

        data = {
            "resource_hash": resource_hash,
            "filename": filename,
            "chunks": records,
        }

        chunk_path = self.chunk_path(resource_hash)
        with chunk_path.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)

        self._manifest[resource_hash] = {
            "chunk_count": len(records),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "filename": filename,
        }
        self._write_manifest()
        logger.debug("Cached %s chunks for %s at %s", len(records), resource_hash, chunk_path)

    def remove(self, resource_hash: str) -> None:
        chunk_path = self.chunk_path(resource_hash)
        try:
            if chunk_path.exists():
                chunk_path.unlink()
        except OSError as exc:
            logger.warning("Failed to delete cached chunks for %s at %s: %s", resource_hash, chunk_path, exc)

        if resource_hash in self._manifest:
            del self._manifest[resource_hash]
            self._write_manifest()

    def prune(self, valid_hashes: Iterable[str]) -> None:
        valid_set = set(valid_hashes)
        stale_hashes = [resource_hash for resource_hash in self._manifest if resource_hash not in valid_set]
        for resource_hash in stale_hashes:
            self.remove(resource_hash)

    def reset(self) -> None:
        for resource_hash in list(self._manifest.keys()):
            self.remove(resource_hash)

    def load_all_documents(self, resource_hashes: Optional[Iterable[str]] = None) -> List[Document]:
        documents: List[Document] = []
        targets = list(resource_hashes) if resource_hashes is not None else list(self._manifest.keys())

        for resource_hash in targets:
            chunk_path = self.chunk_path(resource_hash)
            if not chunk_path.exists():
                logger.debug("Chunk cache file missing for %s; skipping.", resource_hash)
                continue
            try:
                with chunk_path.open("r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load cached chunks for %s: %s", resource_hash, exc)
                continue

            chunks = payload.get("chunks")
            if not isinstance(chunks, list):
                logger.debug("Cached chunks for %s are malformed; skipping.", resource_hash)
                continue

            for record in chunks:
                if not isinstance(record, dict):
                    continue
                page_content = record.get("page_content")
                metadata = record.get("metadata") or {}
                if not isinstance(metadata, dict):
                    logger.debug("Skipping chunk with non-dict metadata for %s", resource_hash)
                    continue
                documents.append(Document(page_content=page_content or "", metadata=metadata))

        return documents
