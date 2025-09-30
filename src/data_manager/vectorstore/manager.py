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
from langchain_community.document_loaders import (
    BSHTMLLoader,
    PyPDFLoader,
    PythonLoader,
    UnstructuredMarkdownLoader,
)
from langchain_community.document_loaders.text import TextLoader
from langchain_text_splitters.character import CharacterTextSplitter

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

        files_in_vstore = [
            metadata["filename"]
            for metadata in collection.get(include=["metadatas"])["metadatas"]
        ]

        dirs = [
            os.path.join(self.data_path, directory)
            for directory in os.listdir(self.data_path)
            if os.path.isdir(os.path.join(self.data_path, directory))
            and directory != "vstore"
        ]
        files_in_data_fullpath = [
            os.path.join(directory, file)
            for directory in dirs
            for file in os.listdir(directory)
        ]
        files_in_data = {
            os.path.basename(path): path for path in files_in_data_fullpath
        }

        sources_path = os.path.join(self.data_path, "sources.yml")
        try:
            with open(sources_path, "r") as file:
                sources = yaml.load(file, Loader=yaml.FullLoader) or {}
        except FileNotFoundError:
            sources = {}

        if set(files_in_data.keys()) == set(files_in_vstore):
            logger.info("Vectorstore is up to date")
        else:
            logger.info("Vectorstore needs to be updated")

            files_to_remove = list(set(files_in_vstore) - set(files_in_data.keys()))
            logger.info(f"Files to remove: {files_to_remove}")
            collection = self._remove_from_vectorstore(collection, files_to_remove)

            files_to_add = {
                filename: files_in_data[filename]
                for filename in set(files_in_data.keys()) - set(files_in_vstore)
            }
            logger.info(f"Files to add: {files_to_add}")
            collection = self._add_to_vectorstore(collection, files_to_add, sources)
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

    def _remove_from_vectorstore(self, collection, files_to_remove: List[str]):
        for filename in files_to_remove:
            collection.delete(where={"filename": filename})
        return collection

    def _add_to_vectorstore(
        self,
        collection,
        files_to_add: Dict[str, str],
        sources: Dict[str, str],
    ):
        for filename, file_path in files_to_add.items():
            logger.info(f"Processing file: {filename}")

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
                    metadatas.append(dict(doc.metadata))

            filehash = filename.split(".")[0]
            url = sources.get(filehash, "")
            logger.info(f"Corresponding: {filename} {filehash} -> {url}")

            embeddings = self.embedding_model.embed_documents(chunks)

            for metadata in metadatas:
                metadata["filename"] = filename

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

            logger.info(f"Successfully added file {filename}")
            if url:
                logger.info(f"with URL: {url}")

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
