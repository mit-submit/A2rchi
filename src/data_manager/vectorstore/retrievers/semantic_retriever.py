from typing import Any, Dict, List

from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores.base import VectorStore

from src.utils.logging import get_logger
from src.data_manager.vectorstore.retrievers.utils import supports_instructions, make_instruction_query, INSTRUCTION_AWARE_MODELS

logger = get_logger(__name__)

class SemanticRetriever(BaseRetriever):
    vectorstore: VectorStore = None
    k: int = 3
    instructions: str | None = None
    dm_config: Dict[str, any] = None
    
    def __init__(self, vectorstore: VectorStore, dm_config: Dict[str, any], k: int = 3, instructions: str | None = None):
        super().__init__()
        self.vectorstore = vectorstore
        self.k = k
        self.instructions = instructions
        self.dm_config = dm_config

    def _get_relevant_documents(self, query: str) -> List[Document]:
        """
        Internal method to retrieve relevant documents based on the query.
        """
        logger.info(f"Retrieving top-{self.k} docs")
        embedding_name = self.dm_config["embedding_name"]
        embedding_model, supported = supports_instructions(embedding_name, self.dm_config)
        
        if self.instructions and supported:
            logger.info(f"Adding instructions to query")
            query = make_instruction_query(self.instructions, query)
        elif self.instructions:
            logger.warning(f"Instructions provided but model '{embedding_model}' not in supported models: {INSTRUCTION_AWARE_MODELS}")
            
        similarity_result = self.vectorstore.similarity_search_with_score(query, k=self.k)
        logger.debug("=== Similarity Search Results ===")
        logger.debug(f"Query: {query}")
        logger.debug(f"Using embedding model: {embedding_model}")
        for d, s in similarity_result:
            logger.debug(f"Doc: {d.metadata['filename']} Score: {s}")
            logger.debug(f"Content: {d.page_content[:150]}...")
        return similarity_result
