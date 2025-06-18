from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores.base import VectorStore
from langchain_core.documents import Document
from typing import Dict, Any, List

# make here configurable which similarity search to use from config...

class SubMITRetriever(BaseRetriever):
    vectorstore: VectorStore
    search_kwargs: Dict[str, Any]
    
    def __init__(self, vectorstore: VectorStore, search_kwargs: dict = None):
        super().__init__(vectorstore=vectorstore, search_kwargs=search_kwargs or {'k': 3})

    def _get_relevant_documents(self, query: str, *, run_manager) -> List[Document]:
        """
        Internal method to retrieve relevant documents based on the query.
        """
        print(f"[SubMITRetriever] Retrieving top-{self.search_kwargs.get('k')} docs")
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
        print(f"[GradingRetriever] Retrieving top-{self.search_kwargs.get('k')} docs")
        return self.vectorstore.similarity_search(query, **self.search_kwargs)