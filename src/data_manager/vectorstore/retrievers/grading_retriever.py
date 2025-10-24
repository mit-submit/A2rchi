from typing import Any, Dict, List

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores.base import VectorStore

from src.utils.logging import get_logger

logger = get_logger(__name__)

class GradingRetriever(BaseRetriever):
    vectorstore: VectorStore = None
    search_kwargs: Dict[str, Any] = None
    
    def __init__(self, vectorstore: VectorStore, search_kwargs: dict = None):
        super().__init__()
        self.vectorstore = vectorstore
        self.search_kwargs = search_kwargs or {'k': 3}

    def _get_relevant_documents(self, query: str) -> List[Document]:
        """
        Retrieve relevant documents based on the query.
        """
        logger.info(f"Retrieving top-{self.search_kwargs.get('k')} docs")
        return self.vectorstore.similarity_search(query, **self.search_kwargs)
    