from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks.manager import CallbackManagerForRetrieverRun
from langchain_core.vectorstores.base import VectorStore
from langchain_core.documents import Document
from typing import Dict, Any, List

from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)

class SubMITRetriever(BaseRetriever):
    vectorstore: VectorStore
    search_kwargs: Dict[str, Any]
    
    def __init__(self, vectorstore: VectorStore, search_kwargs: dict = None):
        super().__init__(vectorstore=vectorstore, search_kwargs=search_kwargs or {'k': 3})

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None) -> List[Document]:
        """
        Internal method to retrieve relevant documents based on the query.
        """
        logger.info(f"Retrieving top-{self.search_kwargs.get('k')} docs")
        return self.vectorstore.similarity_search(query, **self.search_kwargs)


class GradingRetriever(BaseRetriever):
    vectorstore: VectorStore
    search_kwargs: Dict[str, Any]
    
    def __init__(self, vectorstore: VectorStore, search_kwargs: dict = None):
        super().__init__(vectorstore=vectorstore, search_kwargs=search_kwargs or {'k': 3})

    def _get_relevant_documents(self, query: str, *, run_manager) -> List[Document]:
        """
        Retrieve relevant documents based on the query.
        """
        logger.info(f"Retrieving top-{self.search_kwargs.get('k')} docs")
        return self.vectorstore.similarity_search(query, **self.search_kwargs)