from pathlib import Path
from typing import Any, Dict, List, Optional

import nltk
from langchain_core.callbacks.manager import CallbackManagerForRetrieverRun
from langchain_classic.retrievers import EnsembleRetriever
from langchain_community.retrievers import BM25Retriever
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.vectorstores.base import VectorStore

from src.data_manager.vectorstore.chunk_cache import ChunkCache
from src.utils.logging import get_logger

logger = get_logger(__name__)

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
    chunk_cache_path: Optional[str] = None
    chunk_cache: Optional[ChunkCache] = None
    
    def __init__(self, vectorstore: VectorStore, search_kwargs: dict = None, 
                 bm25_weight: float = 0.6, semantic_weight: float = 0.4,
                 bm25_k1: float = 0.5, bm25_b: float = 0.75,
                 chunk_cache_path: Optional[str] = None):
        super().__init__(
            vectorstore=vectorstore, 
            search_kwargs=search_kwargs or {'k': 3},
            bm25_weight=bm25_weight,
            semantic_weight=semantic_weight,
            bm25_k1=bm25_k1,
            bm25_b=bm25_b
        )
        self.chunk_cache_path = chunk_cache_path
        if self.chunk_cache_path:
            cache_path = Path(self.chunk_cache_path)
            try:
                self.chunk_cache = ChunkCache(cache_path)
                logger.debug("Initialised chunk cache at %s for HybridRetriever.", cache_path)
            except Exception as exc:
                logger.warning("Failed to initialise chunk cache at %s: %s", cache_path, exc)
                self.chunk_cache = None
        self._initialize_retrievers()
    
    def _load_corpus_documents(self) -> List[Document]:
        if self.chunk_cache:
            cached_docs = self.chunk_cache.load_all_documents()
            if cached_docs:
                logger.debug("Loaded %s documents for BM25 corpus from chunk cache.", len(cached_docs))
                return cached_docs
            logger.info("Chunk cache at %s is empty; falling back to vectorstore.", self.chunk_cache_path)
        return self._get_all_documents_from_vectorstore()

    def _initialize_retrievers(self):
        """
        Initialize BM25 and ensemble retrievers with documents from the vectorstore.
        """
        try:
            # Get all documents from the vectorstore to build BM25 corpus
            all_docs = self._load_corpus_documents()
            
            if all_docs:
                
                self._bm25_retriever = BM25Retriever.from_documents(all_docs)
                logger.debug(f"BM25Retriever created successfully with {len(all_docs)} documents")

                # k is the number of documents to retrieve from the BM25 retriever
                self._bm25_retriever.k = self.search_kwargs.get('k', 3)
                    
                # Try to set custom parameters for better rare term retrieval
                try:
                    if hasattr(self._bm25_retriever, 'k1'):
                        self._bm25_retriever.k1 = self.bm25_k1  # Use configured k1
                        logger.debug(f"Set BM25 k1 parameter to {self.bm25_k1} for better rare term retrieval")
                    if hasattr(self._bm25_retriever, 'b'):
                        self._bm25_retriever.b = self.bm25_b  # Use configured b
                        logger.debug(f"Set BM25 b parameter to {self.bm25_b}")
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
            logger.error("Falling back to semantic search only")
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
                    
                    logger.debug(f"Retrieved {len(documents)} documents for BM25 corpus")
                    return documents
                else:
                    logger.warning("No documents found in ChromaDB collection")
                    return []
            else:
                logger.warning("ChromaDB collection does not have 'get' method")
                return []
                
        except Exception as e:
            logger.error(f"Error getting documents from ChromaDB: {e}")
        return []
    
    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun = None) -> List[Document]:
        """
        Retrieve relevant documents using hybrid search (BM25 + semantic).
        Falls back to semantic search only if hybrid search is not available.
        """
        logger.debug(f"Query: {query}")
        if self._ensemble_retriever is not None:
            logger.debug(f"Using hybrid search (BM25 + semantic) to retrieve top-{self.search_kwargs.get('k')} docs")
            
            try:                
                bm25_docs = self._bm25_retriever._get_relevant_documents(query, run_manager=run_manager)
                logger.debug(f"BM25 retrieved {len(bm25_docs)} documents")
                
                semantic_docs = self.vectorstore.similarity_search_with_score(query, k=self.search_kwargs.get('k', 3))
                logger.debug(f"Semantic retrieved {len(semantic_docs)} documents")
                
            except Exception as e:
                logger.error(f"Error getting individual retriever results: {e}")
            
            # Get combined results from ensemble
            ensemble_docs = self._ensemble_retriever._get_relevant_documents(query, run_manager=run_manager)
            logger.debug(f"Ensemble returned {len(ensemble_docs)} final documents")
            
            # Return placeholder scores for hybrid search
            logger.debug("Using placeholder score (-1) for hybrid search results")
            docs_with_scores = self._compute_hybrid_scores(ensemble_docs, query)
            
            return docs_with_scores
        else:
            logger.warning(f"Falling back to semantic search only, retrieving top-{self.search_kwargs.get('k')} docs")
            return self.vectorstore.similarity_search_with_score(query, **self.search_kwargs)
    
    def _compute_hybrid_scores(self, ensemble_docs, query):
        """
        Return hardcoded -1 scores for hybrid search.
        This is a temporary placeholder until proper hybrid scoring is implemented.
        The -1 indicates to users that these scores are not yet calibrated.
        """
        docs_with_scores = []
        
        for doc in ensemble_docs:
            # Use -1 as a placeholder score to indicate scores are not yet properly implemented
            docs_with_scores.append((doc, -1.0))
            
            logger.debug(f"Doc: {doc.metadata.get('filename', 'unknown')[:50]}... Score=-1.0 (placeholder)")
        
        return docs_with_scores
