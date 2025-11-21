from typing import Any, Dict, List

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores.base import VectorStore

from src.utils.logging import get_logger

logger = get_logger(__name__)

class GradingRetriever(BaseRetriever):
    vectorstore: VectorStore = None
    k: int
    
    def __init__(self, vectorstore: VectorStore, k: int = 3):
        super().__init__()
        self.vectorstore = vectorstore
        self.k = k

    def _get_relevant_documents(self, query: str) -> List[Document]:
        """
        Retrieve relevant documents based on the query.
        """
        logger.info(f"Retrieving top-{self.k} docs")
        return self.vectorstore.similarity_search(query, k=self.k)
    
