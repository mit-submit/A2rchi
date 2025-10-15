from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from typing import Dict, List

import chromadb
import nltk
import yaml
from chromadb.config import Settings
from langchain_community.document_loaders import (BSHTMLLoader, PyPDFLoader,
                                                  PythonLoader,
                                                  UnstructuredMarkdownLoader)
from langchain_community.document_loaders.text import TextLoader
from langchain_text_splitters.character import CharacterTextSplitter

from src.data_manager.collectors.utils.index_utils import load_sources_catalog
from src.utils.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_DISTANCE_METRICS = ["l2", "cosine", "ip"]


class VectorStoreManager:
    """Encapsulates vectorstore configuration and synchronization."""

    def __init__(self, *, config: Dict, global_config: Dict, data_path: str) -> None:
        self.config = config
        self.global_config = global_config
        self.data_path = data_path

        self._data_manager_config = config["data_manager"]
        self._services_config = config.get("services", {})

        embedding_name = self._data_manager_config["embedding_name"]
        self.collection_name = (
            f"{self._data_manager_config['collection_name']}_with_{embedding_name}"
        )

        self.distance_metric = self._data_manager_config["distance_metric"]
        if self.distance_metric not in SUPPORTED_DISTANCE_METRICS:
            raise ValueError(
                f"The selected distance metrics, '{self.distance_metric}', is not supported. "
                f"Must be one of {SUPPORTED_DISTANCE_METRICS}"
            )

        # Build embedding model
        embedding_class_map = self._data_manager_config["embedding_class_map"]
        embedding_entry = embedding_class_map[embedding_name]
        embedding_class = embedding_entry["class"]
        embedding_kwargs = embedding_entry.get("kwargs", {})
        self.embedding_model = embedding_class(**embedding_kwargs)

        self.text_splitter = CharacterTextSplitter(
            chunk_size=self._data_manager_config["chunk_size"],
            chunk_overlap=self._data_manager_config["chunk_overlap"],
        )

        self.stemmer = None
        stemming_cfg = self._data_manager_config.get("stemming", {})
        if stemming_cfg.get("enabled", False):
            nltk.download("punkt_tab")
            self.stemmer = nltk.stem.PorterStemmer()

    def delete_existing_collection_if_reset(self) -> None:
        """Delete the collection if reset_collection is enabled."""
        if not self._data_manager_config.get("reset_collection", False):
            return

        client = self._build_client()

        if self.collection_name in [c.name for c in client.list_collections()]:
            client.delete_collection(self.collection_name)

    def fetch_collection(self):
        """Return the active Chroma collection."""
        client = self._build_client()
        collection = client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": self.distance_metric},
        )
        logger.info(f"N in collection: {collection.count()}")
        return collection

    def update_vectorstore(self) -> None:
        """Synchronise filesystem documents with the vectorstore."""
        collection = self.fetch_collection()

        sources = load_sources_catalog(self.data_path)
        collection_metadatas = collection.get(include=["metadatas"]).get("metadatas", [])
        files_in_vstore = self._collect_vstore_documents(collection_metadatas)
        files_in_data = self._collect_indexed_documents(sources)

        hashes_in_vstore = set(files_in_vstore.keys())
        hashes_in_data = set(files_in_data.keys())

        if hashes_in_data == hashes_in_vstore:
            logger.info("Vectorstore is up to date")
        else:
            logger.info("Vectorstore needs to be updated")

            hashes_to_remove = list(hashes_in_vstore - hashes_in_data)
            if hashes_to_remove:
                files_to_remove = {
                    hash_value: files_in_vstore.get(hash_value, "<unknown>")
                    for hash_value in hashes_to_remove
                }
                logger.info(f"Resources to remove: {files_to_remove}")
                collection = self._remove_from_vectorstore(collection, hashes_to_remove)

            hashes_to_add = hashes_in_data - hashes_in_vstore
            files_to_add = {
                hash_value: files_in_data[hash_value] for hash_value in hashes_to_add
            }
            logger.info(f"Files to add: {files_to_add}")
            collection = self._add_to_vectorstore(collection, files_to_add)
            logger.info("Vectorstore update has been completed")

        logger.info(f"N Collection: {collection.count()}")
        del collection

    def _build_client(self):
        chroma_cfg = self._services_config.get("chromadb", {})
        if chroma_cfg.get("use_HTTP_chromadb_client"):
            return chromadb.HttpClient(
                host=chroma_cfg["chromadb_host"],
                port=chroma_cfg["chromadb_port"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),
            )

        local_path = chroma_cfg.get(
            "local_vstore_path", self.global_config.get("LOCAL_VSTORE_PATH")
        )
        return chromadb.PersistentClient(
            path=local_path,
            settings=Settings(allow_reset=True, anonymized_telemetry=False),
        )

    def _remove_from_vectorstore(self, collection, hashes_to_remove: List[str]):
        for resource_hash in hashes_to_remove:
            collection.delete(where={"resource_hash": resource_hash})
        return collection

    def _add_to_vectorstore(
        self,
        collection,
        files_to_add: Dict[str, str],
    ):
        for filehash, file_path in files_to_add.items():
            filename = Path(file_path).name
            logger.info(f"Processing file: {filename} (hash: {filehash})")

            try:
                loader = self.loader(file_path)
            except Exception as exc:
                logger.error(
                    f"Failed to load file: {file_path}. Skipping. Exception: {exc}"
                )
                loader = None

            if loader is None:
                continue

            chunks: List[str] = []
            metadatas: List[Dict] = []

            file_level_metadata = self._load_file_metadata(file_path)

            docs = loader.load()
            for doc in docs:
                new_chunks = [
                    document.page_content
                    for document in self.text_splitter.split_documents([doc])
                ]

                for chunk in new_chunks:
                    if self._data_manager_config.get("stemming", {}).get("enabled", False):
                        words = nltk.tokenize.word_tokenize(chunk)
                        stemmed_words = [self.stemmer.stem(word) for word in words]
                        chunk = " ".join(stemmed_words)
                    chunks.append(chunk)
                    entry_metadata = dict(doc.metadata)
                    if file_level_metadata:
                        for key, value in file_level_metadata.items():
                            entry_metadata.setdefault(key, value)
                    metadatas.append(entry_metadata)

            embeddings = self.embedding_model.embed_documents(chunks)

            for metadata in metadatas:
                metadata["filename"] = filename
                metadata["resource_hash"] = filehash

            ids: List[str] = []
            for chunk in chunks:
                identifier = hashlib.md5()
                identifier.update(chunk.encode("utf-8"))
                chunk_hash = str(int(identifier.hexdigest(), 16))[0:6]
                time_identifier = hashlib.md5()
                time_identifier.update(str(time.time()).encode("utf-8"))
                time_hash = str(int(time_identifier.hexdigest(), 16))[0:6]
                composed_id = f"{filehash}{chunk_hash}{time_hash}"
                while composed_id in ids:
                    logger.info(
                        f"Found conflict with hash: {composed_id}. Trying again"
                    )
                    time_hash = str(int(time_hash) + 1)
                    composed_id = f"{filehash}{chunk_hash}{time_hash}"
                ids.append(composed_id)

            logger.debug(f"Ids: {ids}")

            collection.add(
                embeddings=embeddings,
                ids=ids,
                documents=chunks,
                metadatas=metadatas,
            )

            logger.info(f"Successfully added file {filename} (hash: {filehash})")

        return collection

    def loader(self, file_path: str):
        """Return the document loader for a given path."""
        _, file_extension = os.path.splitext(file_path)
        if file_extension in {".txt", ".C"}:
            return TextLoader(file_path)
        if file_extension == ".md":
            return UnstructuredMarkdownLoader(file_path)
        if file_extension == ".py":
            return PythonLoader(file_path)
        if file_extension == ".html":
            return BSHTMLLoader(file_path, bs_kwargs={"features": "html.parser"})
        if file_extension == ".pdf":
            return PyPDFLoader(file_path)

        logger.error(f"Format not supported -- {file_path}")
        return None

    def _collect_indexed_documents(self, sources: Dict[str, str]) -> Dict[str, str]:
        """
        Build a mapping of resource hash -> absolute path from the persisted index.
        """
        files_in_data: Dict[str, str] = {}
        for resource_hash, stored_path in sources.items():
            path = Path(stored_path)
            if not path.exists():
                logger.warning(
                    f"Indexed resource '{resource_hash}' points to missing file: {stored_path}"
                )
                continue
            if path.is_dir():
                logger.debug(
                    f"Indexed resource '{resource_hash}' points to a directory; skipping."
                )
                continue

            if resource_hash in files_in_data and files_in_data[resource_hash] != str(path):
                logger.warning(
                    "Duplicate resource hash detected in index; keeping first occurrence. "
                    f"hash={resource_hash}, existing={files_in_data[resource_hash]}, ignored={path}"
                )
                continue

            files_in_data[resource_hash] = str(path)

        return files_in_data

    def _collect_vstore_documents(self, metadatas: List[Dict]) -> Dict[str, str]:
        """
        Build a mapping of resource hash -> filename currently stored in the vectorstore.
        """
        files_in_vstore: Dict[str, str] = {}
        for metadata in metadatas:
            filename = metadata.get("filename")
            filehash = metadata.get("resource_hash")
            if not filename or not filehash:
                continue
            files_in_vstore.setdefault(filehash, filename)
        return files_in_vstore

    def _load_file_metadata(self, file_path: str) -> Dict[str, str]:
        """
        Load persisted metadata stored alongside the document, if available.
        """
        path = Path(file_path)
        meta_path = path.with_suffix(f"{path.suffix}.meta.yaml")

        if not meta_path.exists():
            return {}

        try:
            with meta_path.open("r", encoding="utf-8") as fh:
                metadata = yaml.safe_load(fh) or {}
        except (yaml.YAMLError, OSError) as exc:
            logger.warning(f"Failed to load metadata for {file_path}: {exc}")
            return {}

        if not isinstance(metadata, dict):
            logger.warning(
                f"Metadata file {meta_path} does not contain a mapping; ignoring."
            )
            return {}

        sanitized: Dict[str, str] = {}
        for key, value in metadata.items():
            if key is None:
                continue
            key_str = str(key)

            if value is None:
                continue
            sanitized[key_str] = str(value)

        return sanitized
