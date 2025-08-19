from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks.manager import CallbackManagerForRetrieverRun
from langchain_core.vectorstores.base import VectorStore
from langchain_core.documents import Document
from typing import Dict, Any, List, Tuple

from a2rchi.utils.config_loader import load_config
from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)

config = load_config()

INSTRUCTION_AWARE_MODELS = [
    "Qwen/Qwen3-Embedding-0.6B",
    "Qwen/Qwen3-Embedding-4B",
    "Qwen/Qwen3-Embedding-8B",
]

class SubMITRetriever(BaseRetriever):
    vectorstore: VectorStore = None
    search_kwargs: Dict[str, Any] = None
    instructions: str = None
    utils_config: Dict[str, any] = None
    
    def __init__(self, vectorstore: VectorStore, search_kwargs: dict = None, instructions: str = None):
        super().__init__()
        self.vectorstore = vectorstore
        self.search_kwargs = search_kwargs or {'k': 3}
        self.instructions = instructions
        self.utils_config = load_config()["utils"]

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None) -> List[Document]:
        """
        Internal method to retrieve relevant documents based on the query.
        """
        logger.info(f"Retrieving top-{self.search_kwargs.get('k')} docs")
        embedding_name = self.utils_config["embeddings"]["EMBEDDING_NAME"]
        embedding_model, supported = supports_instructions(embedding_name)
        
        if self.instructions and supported:
            logger.info(f"Adding instructions to query")
            query = make_instruction_query(self.instructions, query)
        elif self.instructions:
            logger.warning(f"Instructions provided but model '{embedding_model}' not in supported models: {INSTRUCTION_AWARE_MODELS}")
            
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
    
        
def supports_instructions(embedding_name: str) -> Tuple[str, bool]:
    embedding_kwargs = config["utils"]["embeddings"]["EMBEDDING_CLASS_MAP"][embedding_name]["kwargs"]
    embedding_model = embedding_kwargs.get("model") or embedding_kwargs.get("model_name")
    return embedding_model, embedding_model in INSTRUCTION_AWARE_MODELS

def make_instruction_query(instructions: str, query: str) -> str:
    return f"Instruct: {instructions}\nQuery:{query}"