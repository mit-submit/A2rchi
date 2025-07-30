from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks.manager import CallbackManagerForRetrieverRun
from langchain_core.vectorstores.base import VectorStore
from langchain_core.documents import Document
from typing import Dict, Any, List

from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)

INSTRUCTION_AWARE_MODELS = [
    "Qwen/Qwen3-Embedding-0.6B",
    "Qwen/Qwen3-Embedding-4B",
    "Qwen/Qwen3-Embedding-8B",
]

class SubMITRetriever(BaseRetriever):
    vectorstore: VectorStore = None
    search_kwargs: Dict[str, Any] = None
    instructions: str = None
    
    def __init__(self, vectorstore: VectorStore, search_kwargs: dict = None, instructions: str = None):
        super().__init__()
        self.vectorstore = vectorstore
        self.search_kwargs = search_kwargs or {'k': 3}
        self.instructions = instructions

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None) -> List[Document]:
        """
        Internal method to retrieve relevant documents based on the query.
        """
        logger.info(f"Retrieving top-{self.search_kwargs.get('k')} docs")
        if self.instructions and supports_instructions(self.vectorstore._embedding_function.model_name):
            logger.info(f"Adding instructions to query")
            instructed_query = make_instruction_query(self.instructions, query)
            return self.vectorstore.similarity_search(instructed_query, **self.search_kwargs)
        else:
            return self.vectorstore.similarity_search(query, **self.search_kwargs)


class GradingRetriever(BaseRetriever):
    vectorstore: VectorStore = None
    search_kwargs: Dict[str, Any] = None
    
    def __init__(self, vectorstore: VectorStore, search_kwargs: dict = None):
        super().__init__()
        self.vectorstore = vectorstore
        self.search_kwargs = search_kwargs or {'k': 3}

    def _get_relevant_documents(self, query: str, *, run_manager) -> List[Document]:
        """
        Retrieve relevant documents based on the query.
        """
        logger.info(f"Retrieving top-{self.search_kwargs.get('k')} docs")
        return self.vectorstore.similarity_search(query, **self.search_kwargs)
    
        
def supports_instructions(model_name: str) -> bool:
    return model_name in INSTRUCTION_AWARE_MODELS

def make_instruction_query(instructions: str, query: str) -> str:
    return f"Instruct: {instructions}\nQuery:{query}"