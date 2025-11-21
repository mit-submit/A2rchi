from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from langchain_core.documents import Document

import yaml

from src.utils.logging import get_logger
from src.data_manager.vectorstore.loader_utils import load_doc_from_path

logger = get_logger(__name__)

DEFAULT_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".rst",
    ".pdf",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
    ".tsv",
    ".html",
    ".htm",
    ".log",
}

@dataclass
class CatalogService:
    """Expose lightweight access to catalogued resources and metadata."""

    data_path: Path | str
    include_extensions: Sequence[str] = field(default_factory=lambda: sorted(DEFAULT_TEXT_EXTENSIONS))
    filename: str = "index.yaml"
    metadata_filename: str = "metadata_index.yaml"
    _file_index: Dict[str, str] = field(init=False, default_factory=dict)
    _metadata_index: Dict[str, str] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.data_path = Path(self.data_path)
        if self.include_extensions:
            self.include_extensions = tuple(ext.lower() for ext in self.include_extensions)
        self.refresh()

    def refresh(self) -> None:
        """Reload file and metadata indices from disk."""
        logger.debug("Refreshing catalog indices from %s", self.data_path)
        self._file_index = self.load_index(self.data_path, filename=self.filename)
        self._metadata_index = self.load_index(self.data_path, filename=self.metadata_filename)

    @property
    def file_index(self) -> Dict[str, str]:
        return self._file_index

    @property
    def metadata_index(self) -> Dict[str, str]:
        return self._metadata_index

    def get_resource_hashes_by_metadata_filter(self, metadata_field: str, value: str) -> List[str]:
        """
        Return resource hashes whose metadata contains ``metadata_field`` equal to ``value``.
        """
        matches: List[str] = []
        for resource_hash in self._metadata_index:
            metadata = self.get_metadata_for_hash(resource_hash)
            if not metadata:
                continue
            if metadata.get(metadata_field) == value:
                matches.append(resource_hash)
        return matches

    def iter_files(self) -> Iterable[Tuple[str, Path]]:
        for resource_hash in self._file_index.keys():
            path = self.get_filepath_for_hash(resource_hash)
            if not path:
                continue
            if self.include_extensions and path.suffix.lower() not in self.include_extensions:
                continue
            yield resource_hash, path

    def metadata_path_for_hash(self, resource_hash: str) -> Optional[Path]:
        stored = self._metadata_index.get(resource_hash)
        if not stored:
            return None
        metadata_path = Path(stored)
        if not metadata_path.is_absolute():
            metadata_path = (self.data_path / metadata_path).resolve()
        return metadata_path if metadata_path.exists() else None
    
    def get_metadata_for_hash(self, hash: str) -> Optional[Dict[str, any]]:
        metadata_path = self.metadata_path_for_hash(hash)
        if not metadata_path:
            return None
        try:
            with metadata_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
            if not isinstance(data, dict):
                logger.warning(f"Metadata file {metadata_path} does not contain a mapping; returning None.")
                return None
            return data
        except yaml.YAMLError as exc:
            logger.warning(f"Failed to parse metadata file {metadata_path}: {exc}")
            return None

    def get_filepath_for_hash(self, hash: str) -> Optional[Path]:
        stored = self._file_index.get(hash)
        if not stored:
            return None
        path = Path(stored)
        if not path.is_absolute():
            path = (self.data_path / path).resolve()
        return path if path.exists() else None

    # TODO this should probably be in the persistence service (?)
    def get_document_for_hash(self, hash: str) -> Optional[Document]:
        """
        Reconstruct a Document for the given resource hash, combining content and metadata.
        """
        path = self.get_filepath_for_hash(hash)
        if not path:
            return None
        doc = load_doc_from_path(path)
        metadata = self.get_metadata_for_hash(hash)
        if doc and metadata:
            doc.metadata.update(metadata)
        return doc

    @classmethod
    def load_index(cls, data_path: Path | str, filename: Optional[str] = None) -> Dict[str, str]:
        """
        Load the unified resource index from the provided YAML file located within ``data_path``.
        Returns an empty mapping if the file does not exist or cannot be parsed.
        """
        base_path = Path(data_path)
        target_filename = filename or cls.filename
        index_path = base_path / target_filename

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

    @classmethod
    def load_sources_catalog(cls, data_path: Path | str, filename: Optional[str] = None) -> Dict[str, str]:
        """
        Convenience helper that returns the resource index mapping with absolute paths.
        """
        base_path = Path(data_path)
        index = cls.load_index(base_path, filename=filename)
        resolved: Dict[str, str] = {}
        for key, stored_path in index.items():
            path = Path(stored_path)
            if not path.is_absolute():
                path = (base_path / path).resolve()
            resolved[key] = str(path)
        return resolved

    @classmethod
    def write_index(
        cls, data_path: Path | str, index_data: Dict[str, str], filename: Optional[str] = None
    ) -> None:
        """
        Persist the provided index to the given YAML file inside ``data_path``.
        """
        base_path = Path(data_path)
        target_filename = filename or cls.filename
        index_path = base_path / target_filename
        index_path.parent.mkdir(parents=True, exist_ok=True)

        with index_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(index_data, fh, sort_keys=True)
