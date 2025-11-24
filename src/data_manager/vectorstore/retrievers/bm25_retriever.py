from typing import Any, Dict, List, Optional

from langchain_core.callbacks.manager import CallbackManagerForRetrieverRun
from langchain_community.retrievers import BM25Retriever as LangChainBM25Retriever
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores.base import VectorStore

from src.utils.logging import get_logger

logger = get_logger(__name__)


class BM25LexicalRetriever(BaseRetriever):
    """
    Lightweight wrapper around LangChain's BM25 retriever that builds the corpus
    from the configured vectorstore and exposes the BaseRetriever interface.
    """

    vectorstore: VectorStore
    k: int
    bm25_k1: float
    bm25_b: float
    _bm25_retriever: Optional[LangChainBM25Retriever] = None

    def __init__(
        self,
        vectorstore: VectorStore,
        k: int = 3,
        bm25_k1: float = 0.5,
        bm25_b: float = 0.75,
    ):
        super().__init__(
            vectorstore=vectorstore,
            k=k,
            bm25_k1=bm25_k1,
            bm25_b=bm25_b,
        )
        self.k = k
        self._initialize_retriever()

    @property
    def ready(self) -> bool:
        """Return True when the underlying BM25 retriever has been initialised."""
        return self._bm25_retriever is not None

    def _initialize_retriever(self) -> None:
        """Build the BM25 retriever from documents in the vectorstore."""
        try:
            documents = self._load_corpus_documents()
            if not documents:
                logger.warning("No documents found for BM25 corpus; skipping BM25 setup.")
                return

            bm25_retriever = LangChainBM25Retriever.from_documents(documents)
            logger.debug("BM25 retriever created with %s documents", len(documents))

            bm25_retriever.k = self.k
            try:
                if hasattr(bm25_retriever, "k1"):
                    bm25_retriever.k1 = self.bm25_k1
                if hasattr(bm25_retriever, "b"):
                    bm25_retriever.b = self.bm25_b
            except Exception as param_error:
                logger.warning("Could not set custom BM25 parameters: %s", param_error)

            self._bm25_retriever = bm25_retriever
        except Exception as exc:
            logger.error("Failed to initialize BM25 retriever: %s", exc)
            self._bm25_retriever = None

    def _load_corpus_documents(self) -> List[Document]:
        documents = self._get_all_documents_from_vectorstore()
        if documents:
            logger.debug(
                "Loaded %s documents for BM25 corpus from vectorstore.", len(documents)
            )
        else:
            logger.warning("Vectorstore returned no documents for BM25 corpus.")
        return documents

    def _get_all_documents_from_vectorstore(self) -> List[Document]:
        """
        Get all documents from the vectorstore to build BM25 corpus.
        Uses ChromaDB's collection.get() method to retrieve all documents.
        """
        try:
            if hasattr(self.vectorstore, "_collection"):
                collection = self.vectorstore._collection
            elif hasattr(self.vectorstore, "collection"):
                collection = self.vectorstore.collection
            else:
                logger.warning("Could not access ChromaDB collection directly")
                return []

            if hasattr(collection, "get"):
                results = collection.get()
                if results and "documents" in results and results["documents"]:
                    documents = []
                    for i, doc_content in enumerate(results["documents"]):
                        metadata: Dict[str, Any] = {}
                        if (
                            "metadatas" in results
                            and results["metadatas"]
                            and i < len(results["metadatas"])
                        ):
                            metadata = results["metadatas"][i] or {}

                        documents.append(
                            Document(
                                page_content=doc_content,
                                metadata=metadata,
                            )
                        )

                    logger.debug("Retrieved %s documents for BM25 corpus", len(documents))
                    return documents

                logger.warning("No documents found in ChromaDB collection")
                return []

            logger.warning("ChromaDB collection does not have 'get' method")
            return []

        except Exception as exc:
            logger.error("Error getting documents from ChromaDB: %s", exc)
        return []

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None
    ) -> List[Document]:
        """
        Retrieve documents using the BM25 retriever. Returns an empty list when
        BM25 has not been initialised.
        """
        if not self._bm25_retriever:
            logger.warning("BM25 retriever not initialised; returning no documents.")
            return []

        return self._bm25_retriever._get_relevant_documents(
            query, run_manager=run_manager
        )
