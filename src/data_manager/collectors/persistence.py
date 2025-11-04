from __future__ import annotations

from pathlib import Path
from typing import Dict, TYPE_CHECKING, Union, Any, List

import yaml

from src.data_manager.collectors.utils.index_utils import CatalogService
from src.utils.logging import get_logger
from src.data_manager.collectors.utils.metadata import ResourceMetadata

if TYPE_CHECKING:
    from src.data_manager.collectors.resource_base import BaseResource

logger = get_logger(__name__)


class PersistenceService:
    """Shared filesystem persistence for collected resources."""

    def __init__(self, data_path: Path | str) -> None:
        self.data_path = Path(data_path)

        self._index: Dict[str, str] = CatalogService.load_index(self.data_path)
        self._index_dirty = False

        self._metadata_index: Dict[str, str] = CatalogService.load_index(
            self.data_path, filename="metadata_index.yaml"
        )
        self._metadata_index_dirty = False

    def persist_resource(self, resource: "BaseResource", target_dir: Path) -> Path:
        """
        Write a resource and its metadata to disk,
        updating both indices accordingly: with the unique hash of the file as key for both,
        and the path to the file (metadata file) as value for the main (metadata) index.
        """
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = resource.get_file_path(target_dir)
        content = resource.get_content()
        self._write_content(file_path, content)

        metadata = resource.get_metadata()
        if metadata is not None:
            metadata_path = resource.get_metadata_path(file_path)
            self._write_metadata(metadata_path, metadata)
            try:
                metadata_relative_path = (
                    metadata_path.relative_to(self.data_path).as_posix()
                )
            except ValueError:
                metadata_relative_path = str(metadata_path)

            resource_hash = resource.get_hash()
            self._metadata_index[resource_hash] = metadata_relative_path
            self._metadata_index_dirty = True

        try:
            relative_path = file_path.relative_to(self.data_path).as_posix()
        except ValueError:
            relative_path = str(file_path)

        resource_hash = resource.get_hash()
        logger.info(f"Stored resource {resource_hash} -> {file_path}")
        self._index[resource_hash] = relative_path
        self._index_dirty = True
        return file_path
    
    def delete_resource(self, resource_hash:str) -> Path:
        """
        Delete a resource and its metadata from disk,
        updating both indices accordingly: with the unique hash of the file as key for both,
        and the path to the file (metadata file) as value for the main (metadata) index.
        """
        
        try:
            file_path = self.data_path / self._index[resource_hash]
            metadata_path = self.data_path /self._metadata_index[resource_hash]
        except Exception as e:
            raise ValueError(f"Resource hash {resource_hash} not found. {e}")

        self._delete_content(file_path)
        self._index.pop(resource_hash)
        self._index_dirty = True

        self._delete_metadata(metadata_path)
        self._metadata_index.pop(resource_hash)
        self._metadata_index_dirty = True

        
        logger.info(f"Deleted resource {resource_hash} -> {file_path}")  
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

        try:
            relative_prefix = directory.relative_to(self.data_path)
        except ValueError:
            relative_prefix = None

        if relative_prefix is not None:
            prefix_parts = relative_prefix.parts
            keys_to_remove = []
            for key, stored in self._index.items():
                stored_path = Path(stored)
                if stored_path.is_absolute():
                    try:
                        stored_path = stored_path.relative_to(self.data_path)
                    except ValueError:
                        continue
                if stored_path.parts[: len(prefix_parts)] == prefix_parts:
                    keys_to_remove.append(key)
            if keys_to_remove:
                for key in keys_to_remove:
                    self._index.pop(key, None)
                self._index_dirty = True

            metadata_keys_to_remove = []
            for key, stored in self._metadata_index.items():
                stored_path = Path(stored)
                if stored_path.is_absolute():
                    try:
                        stored_path = stored_path.relative_to(self.data_path)
                    except ValueError:
                        continue
                if stored_path.parts[: len(prefix_parts)] == prefix_parts:
                    metadata_keys_to_remove.append(key)
            if metadata_keys_to_remove:
                for key in metadata_keys_to_remove:
                    self._metadata_index.pop(key, None)
                self._metadata_index_dirty = True

    def flush_index(self) -> None:
        if self._index_dirty:
            CatalogService.write_index(self.data_path, self._index)
            self._index_dirty = False

        if self._metadata_index_dirty:
            CatalogService.write_index(
                self.data_path,
                self._metadata_index,
                filename="metadata_index.yaml",
            )
            self._metadata_index_dirty = False

    def get_resource_hashes_by_metadata_filter(self, metadata_field, value) -> List[str]:

        filtered_hashes = []
        for resource_hash, metadata_path in self._metadata_index.items():
            with open(self.data_path / metadata_path) as metadata_file:
                metadata = yaml.safe_load(metadata_file)
            if metadata_field in metadata.keys() and metadata[metadata_field]==value:
                filtered_hashes.append(resource_hash)


        return filtered_hashes

    def _remove_tree(self, path: Path) -> None:
        for item in path.iterdir():
            if item.is_dir():
                self._remove_tree(item)
            else:
                item.unlink()
        path.rmdir()

    def _write_content(
        self,
        file_path: Path,
        content: Union[str, bytes, bytearray],
    ) -> None:
        if content is None:
            raise ValueError("Resource provided no content to persist")

        if isinstance(content, (bytes, bytearray)):
            payload = bytes(content)
            if not payload:
                raise ValueError("Refusing to persist empty binary content")
            file_path.write_bytes(payload)
            return

        if isinstance(content, str):
            if not content:
                raise ValueError("Refusing to persist empty textual content")
            file_path.write_text(content, encoding="utf-8")
            return

        raise TypeError(
            f"Unsupported content type {type(content)!r}; "
            "resources must return str or bytes"
        )

    def _write_metadata(self, metadata_path: Path, metadata: Any) -> None:
        if type(metadata) != ResourceMetadata:
            raise Exception("Metadata must be of type ResourceMetadata")
        metadata_dict = self._normalise_metadata(metadata)
        if not metadata_dict:
            raise ValueError("Refusing to persist empty metadata payload")

        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        with metadata_path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(metadata_dict, fh, sort_keys=True)

    def _delete_content(self,file_path: Path) -> None:
        file_path.unlink()

    def _delete_metadata(self, metadata_path: Path) -> None:
        metadata_path.unlink()

    @staticmethod
    def _normalise_metadata(metadata: Any) -> Dict[str, str]:
        if hasattr(metadata, "as_dict"):
            metadata_dict = metadata.as_dict()
        elif isinstance(metadata, dict):
            metadata_dict = metadata
        else:
            metadata_dict = {"value": str(metadata)}

        if not isinstance(metadata_dict, dict):
            raise TypeError("Metadata serialisation must produce a dictionary")

        sanitized: Dict[str, str] = {}
        for key, value in metadata_dict.items():
            if value is None:
                continue
            sanitized[str(key)] = str(value)
        return sanitized
