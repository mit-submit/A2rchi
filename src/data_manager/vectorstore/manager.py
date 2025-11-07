from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from .loader_utils import select_loader
from langchain_text_splitters.character import CharacterTextSplitter

from src.data_manager.collectors.utils.index_utils import CatalogService
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

        default_workers = min(64, (os.cpu_count() or 1) + 4)
        parallel_workers_config = self._data_manager_config.get("parallel_workers")
        if parallel_workers_config is None:
            self.parallel_workers = default_workers
        else:
            try:
                self.parallel_workers = int(parallel_workers_config)
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid 'parallel_workers' value %r. Falling back to default.",
                    parallel_workers_config,
                )
                self.parallel_workers = default_workers
        self.parallel_workers = max(1, self.parallel_workers)

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

        sources = CatalogService.load_sources_catalog(self.data_path)
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
        if not files_to_add:
            return collection

        files_to_add_items = list(files_to_add.items())
        apply_stemming = self._data_manager_config.get("stemming", {}).get("enabled", False)
        if apply_stemming:
            tokenize = nltk.tokenize.word_tokenize
            stem = self.stemmer.stem

        def process_file(filehash: str, file_path: str):
            filename = Path(file_path).name
            logger.info(f"Processing file: {filename} (hash: {filehash})")

            try:
                loader = self.loader(file_path)
            except Exception as exc:
                logger.error(
                    f"Failed to load file: {file_path}. Skipping. Exception: {exc}"
                )
                return None

            if loader is None:
                return None

            file_level_metadata = self._load_file_metadata(file_path)
            try:
                docs = loader.load()
            except Exception as exc:
                logger.error(
                    "Failed to read file %s. Skipping. Exception: %s",
                    file_path,
                    exc,
                )
                return None

            split_docs = self.text_splitter.split_documents(docs)

            chunks: List[str] = []
            metadatas: List[Dict] = []

            for index, split_doc in enumerate(split_docs):
                chunk = split_doc.page_content or ""
                if apply_stemming:
                    words = tokenize(chunk)
                    chunk = " ".join(stem(word) for word in words)

                if not chunk.strip():
                    continue

                chunks.append(chunk)

                doc_metadata = getattr(split_doc, "metadata", {}) or {}
                if not isinstance(doc_metadata, dict):
                    doc_metadata = dict(doc_metadata)
                entry_metadata = {**file_level_metadata, **doc_metadata}
                entry_metadata["chunk_index"] = index
                metadatas.append(entry_metadata)

            if not chunks:
                logger.info(f"No chunks generated for {filename}; skipping.")
                return None

            return filename, chunks, metadatas

        processed_results: Dict[str, tuple] = {}
        max_workers = max(1, self.parallel_workers)
        logger.info(f"Processing files with up to {max_workers} parallel workers")
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(process_file, filehash, file_path): filehash
                for filehash, file_path in files_to_add_items
            }
            for future in as_completed(futures):
                filehash = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # Defensive: log unexpected failures
                    logger.error(
                        "Unexpected error while processing %s: %s",
                        files_to_add.get(filehash),
                        exc,
                    )
                    continue
                if result:
                    processed_results[filehash] = result

        for filehash, file_path in files_to_add_items:
            processed = processed_results.get(filehash)
            if not processed:
                continue

            filename, chunks, metadatas = processed
            embeddings = self.embedding_model.embed_documents(chunks)

            for metadata in metadatas:
                metadata["filename"] = filename
                metadata["resource_hash"] = filehash

            ids = [f"{filehash}-{idx:06d}" for idx in range(len(chunks))]

            logger.debug(f"Ids: {ids}")

            collection.add(
                embeddings=embeddings,
                ids=ids,
                documents=chunks,
                metadatas=metadatas,
            )

        return collection

    def loader(self, file_path: str):
        """Return the document loader for a given path."""
        loader = select_loader(file_path)
        if loader is None:
            logger.error(f"Format not supported -- {file_path}")
        return loader

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
