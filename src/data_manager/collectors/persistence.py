from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Dict

import yaml

from src.data_manager.collectors.scrapers.scraped_resource import \
    ScrapedResource
from src.data_manager.collectors.tickets.ticket_resource import TicketResource
from src.utils.logging import get_logger

logger = get_logger(__name__)


class PersistenceService:
    """Shared filesystem persistence for collected resources."""

    def __init__(self, data_path: Path | str) -> None:
        self.data_path = Path(data_path)
        self.sources_path = self.data_path / "sources.yml"
        self.websites_dir = self.data_path / "websites"
        self.git_dir = self.data_path / "git"
        self.tickets_dir = self.data_path / "tickets"
        self.tickets_index_path = self.data_path / "tickets.yml"

        for directory in (self.websites_dir, self.git_dir, self.tickets_dir):
            directory.mkdir(parents=True, exist_ok=True)

        self._sources: Dict[str, str] = self._load_sources()
        self._sources_need_flush = False

        self._tickets_index: Dict[str, Dict] = self._load_tickets_index()
        self._tickets_need_flush = False

    def persist_scraped_resource(self, resource: ScrapedResource, target_dir: Path) -> Path:
        """Persist a scraped web resource and update the sources catalogue."""
        target_dir.mkdir(parents=True, exist_ok=True)

        file_id = self._hash_string(resource.url)
        suffix = resource.suffix.lstrip(".")
        file_path = target_dir / f"{file_id}.{suffix}"

        if resource.is_binary:
            content = resource.content if isinstance(resource.content, (bytes, bytearray)) else bytes(resource.content)
            file_path.write_bytes(content)
        else:
            file_path.write_text(str(resource.content))

        logger.info(f"Stored resource {resource.url} -> {file_path}")
        self._sources[file_id] = resource.url
        self._sources_need_flush = True
        return file_path

    def persist_ticket(self, resource: TicketResource) -> Path:
        """Persist a ticket resource and update the ticket index."""
        file_name = f"{resource.source}_{self._normalise_identifier(resource.ticket_id)}.txt"
        file_path = self.tickets_dir / file_name

        file_path.write_text(resource.content, encoding="utf-8")
        logger.info(f"Stored ticket {resource.ticket_id} at {file_path}")

        self._tickets_index[file_name] = resource.to_index_record()
        self._tickets_need_flush = True
        return file_path

    def reset_directory(self, directory: Path) -> None:
        """Remove all files and folders within the specified directory."""
        if not directory.exists():
            return

        for item in directory.iterdir():
            if item.is_file():
                item.unlink()
            else:
                self._remove_tree(item)

    def flush_sources(self) -> None:
        if not self._sources_need_flush:
            return

        with self.sources_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(self._sources, fh)
        self._sources_need_flush = False

    def flush_tickets(self) -> None:
        if not self._tickets_need_flush:
            return

        with self.tickets_index_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(self._tickets_index, fh)
        self._tickets_need_flush = False

    def flush_all(self) -> None:
        self.flush_sources()
        self.flush_tickets()

    def _load_sources(self) -> Dict[str, str]:
        if not self.sources_path.exists():
            return {}

        try:
            with self.sources_path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            logger.warning(f"Failed to parse sources.yml: {exc}")
            return {}

    def _load_tickets_index(self) -> Dict[str, Dict]:
        if not self.tickets_index_path.exists():
            return {}

        try:
            with self.tickets_index_path.open("r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        except yaml.YAMLError as exc:
            logger.warning(f"Failed to parse tickets.yml: {exc}")
            return {}

    def _hash_string(self, value: str) -> str:
        identifier = hashlib.md5()
        identifier.update(value.encode("utf-8"))
        return str(int(identifier.hexdigest(), 16))[0:12]

    def _normalise_identifier(self, identifier: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", identifier)

    def _remove_tree(self, path: Path) -> None:
        for item in path.iterdir():
            if item.is_dir():
                self._remove_tree(item)
            else:
                item.unlink()
        path.rmdir()
