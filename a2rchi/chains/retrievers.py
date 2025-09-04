from langchain_core.retrievers import BaseRetriever
from langchain_core.callbacks.manager import CallbackManagerForRetrieverRun
from langchain_core.vectorstores.base import VectorStore
from langchain_core.documents import Document
from langchain_community.retrievers import BM25Retriever
from langchain.retrievers import EnsembleRetriever

from typing import Dict, Any, List, Tuple

from a2rchi.utils.logging import get_logger

import nltk

logger = get_logger(__name__)

INSTRUCTION_AWARE_MODELS = [
    "Qwen/Qwen3-Embedding-0.6B",
    "Qwen/Qwen3-Embedding-4B",
    "Qwen/Qwen3-Embedding-8B",
]

class SemanticRetriever(BaseRetriever):
    vectorstore: VectorStore = None
    search_kwargs: Dict[str, Any] = None
    instructions: str = None
    dm_config: Dict[str, any] = None
    
    def __init__(self, vectorstore: VectorStore, dm_config: Dict[str, any], search_kwargs: dict = None, instructions: str = None):
        super().__init__()
        self.vectorstore = vectorstore
        self.search_kwargs = search_kwargs or {'k': 3}
        self.instructions = instructions
        self.dm_config = dm_config

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None) -> List[Document]:
        """
        Internal method to retrieve relevant documents based on the query.
        """
        logger.info(f"Retrieving top-{self.search_kwargs.get('k')} docs")
        embedding_name = self.dm_config["embeddings"]["embedding_name"]
        embedding_model, supported = supports_instructions(embedding_name, self.dm_config)
        
        if self.instructions and supported:
            logger.info(f"Adding instructions to query")
            query = make_instruction_query(self.instructions, query)
        elif self.instructions:
            logger.warning(f"Instructions provided but model '{embedding_model}' not in supported models: {INSTRUCTION_AWARE_MODELS}")
            
        similarity_result = self.vectorstore.similarity_search_with_score(query, **self.search_kwargs)
        for d, s in similarity_result:
            logger.debug(f"Doc: {d.metadata['filename']} Score: {s}")
        print(similarity_result)
        return similarity_result

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
    
        
def supports_instructions(embedding_name: str, dm_config: Dict[str, any]) -> Tuple[str, bool]:
    embedding_kwargs = dm_config["embeddings"]["embedding_class_map"][embedding_name]["kwargs"]
    embedding_model = embedding_kwargs.get("model") or embedding_kwargs.get("model_name")
    return embedding_model, embedding_model in INSTRUCTION_AWARE_MODELS

def make_instruction_query(instructions: str, query: str) -> str:
    return f"Instruct: {instructions}\nQuery:{query}"


class HybridRetriever(BaseRetriever):
    """
    Hybrid retriever that combines BM25 (lexical) and ChromaDB (semantic) search.
    """
    vectorstore: VectorStore
    search_kwargs: Dict[str, Any]
    bm25_weight: float = 0.6
    semantic_weight: float = 0.4
    bm25_k1: float = 0.5
    bm25_b: float = 0.75
    _bm25_retriever: BM25Retriever = None
    _ensemble_retriever: EnsembleRetriever = None
    
    def __init__(self, vectorstore: VectorStore, search_kwargs: dict = None, 
                 bm25_weight: float = 0.6, semantic_weight: float = 0.4,
                 bm25_k1: float = 0.5, bm25_b: float = 0.75):
        super().__init__(
            vectorstore=vectorstore, 
            search_kwargs=search_kwargs or {'k': 3},
            bm25_weight=bm25_weight,
            semantic_weight=semantic_weight,
            bm25_k1=bm25_k1,
            bm25_b=bm25_b
        )
        self._initialize_retrievers()
    
    def _initialize_retrievers(self):
        """
        Initialize BM25 and ensemble retrievers with documents from the vectorstore.
        """
        try:
            # Get all documents from the vectorstore to build BM25 corpus
            all_docs = self._get_all_documents_from_vectorstore()
            
            if all_docs:
                
                self._bm25_retriever = BM25Retriever.from_documents(all_docs)
                logger.info(f"BM25Retriever created successfully with {len(all_docs)} documents")

                # k is the number of documents to retrieve from the BM25 retriever
                self._bm25_retriever.k = self.search_kwargs.get('k', 3)
                    
                # Try to set custom parameters for better rare term retrieval
                try:
                    if hasattr(self._bm25_retriever, 'k1'):
                        self._bm25_retriever.k1 = self.bm25_k1  # Use configured k1
                        logger.info(f"Set BM25 k1 parameter to {self.bm25_k1} for better rare term retrieval")
                    if hasattr(self._bm25_retriever, 'b'):
                        self._bm25_retriever.b = self.bm25_b  # Use configured b
                        logger.info(f"Set BM25 b parameter to {self.bm25_b}")
                except Exception as param_error:
                    logger.warning(f"Could not set custom BM25 parameters: {param_error}")
                
                dense_retriever = self.vectorstore.as_retriever(
                    search_kwargs=self.search_kwargs
                )
                
                self._ensemble_retriever = EnsembleRetriever(
                    retrievers=[self._bm25_retriever, dense_retriever],
                    weights=[self.bm25_weight, self.semantic_weight]
                )
 
            else:
                logger.warning("No documents found in vectorstore, falling back to semantic search only")
                self._ensemble_retriever = None
                
        except Exception as e:
            logger.error(f"Failed to initialize hybrid retriever: {e}")
            logger.info("Falling back to semantic search only")
            self._ensemble_retriever = None
    
    def _get_all_documents_from_vectorstore(self) -> List[Document]:
        """
        Get all documents from the vectorstore to build BM25 corpus.
        Uses ChromaDB's collection.get() method to retrieve all documents.
        """
        try:
            # Access the ChromaDB collection directly
            if hasattr(self.vectorstore, '_collection'):
                collection = self.vectorstore._collection
            elif hasattr(self.vectorstore, 'collection'):
                collection = self.vectorstore.collection
            else:
                logger.warning("Could not access ChromaDB collection directly")
                return []
            
            # Get all documents from the collection
            if hasattr(collection, 'get'):
                results = collection.get()
                if results and 'documents' in results and results['documents']:
                    documents = []
                    for i, doc_content in enumerate(results['documents']):
                        # Get metadata if available
                        metadata = {}
                        if 'metadatas' in results and results['metadatas'] and i < len(results['metadatas']):
                            metadata = results['metadatas'][i] or {}
                        
                        document = Document(
                            page_content=doc_content,
                            metadata=metadata
                        )
                        documents.append(document)
                    
                    logger.info(f"Retrieved {len(documents)} documents for BM25 corpus")
                    return documents
                else:
                    logger.warning("No documents found in ChromaDB collection")
                    return []
            else:
                logger.warning("ChromaDB collection does not have 'get' method")
                return []
                
        except Exception as e:
            logger.error(f"Error getting documents from ChromaDB: {e}")
    
    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None) -> List[Document]:
        """
        Retrieve relevant documents using hybrid search (BM25 + semantic).
        Falls back to semantic search only if hybrid search is not available.
        """
        if self._ensemble_retriever is not None:
            logger.info(f"Using hybrid search (BM25 + semantic) to retrieve top-{self.search_kwargs.get('k')} docs")
            
            try:                
                bm25_docs = self._bm25_retriever._get_relevant_documents(query, run_manager=run_manager)
                logger.debug(f"BM25 retrieved {len(bm25_docs)} documents")
                
                logger.debug("=== BM25 Results ===")
                for i, doc in enumerate(bm25_docs[:3]):
                    logger.debug(f"BM25 doc {i+1}: {doc.page_content[:150]}...")
                
                semantic_docs = self.vectorstore.similarity_search_with_score(query, k=self.search_kwargs.get('k', 3))
                logger.debug(f"Semantic retrieved {len(semantic_docs)} documents")
                logger.debug("=== Semantic Results ===")
                for i, (doc, score) in enumerate(semantic_docs[:3]):
                    logger.debug(f"Semantic doc {i+1} (score: {score:.4f}): {doc.page_content[:150]}...")
                
            except Exception as e:
                logger.error(f"Error getting individual retriever results: {e}")
            
            # Get combined results from ensemble
            ensemble_docs = self._ensemble_retriever._get_relevant_documents(query, run_manager=run_manager)
            logger.error(f"Ensemble returned {len(ensemble_docs)} final documents")
            
            return ensemble_docs
        else:
            logger.info(f"Falling back to semantic search only, retrieving top-{self.search_kwargs.get('k')} docs")
            return self.vectorstore.similarity_search(query, **self.search_kwargs)
