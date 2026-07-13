"""
PostgresVectorStore - A LangChain-compatible vector store using PostgreSQL + pgvector.

This replaces ChromaDB for vector similarity search in archi.
Implements the langchain_core.vectorstores.VectorStore interface.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Type

import psycopg2
import psycopg2.extras
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from src.utils.logging import get_logger

logger = get_logger(__name__)


class PostgresVectorStore(VectorStore):
    """
    Vector store implementation using PostgreSQL with pgvector extension.
    
    Stores document chunks with embeddings in the document_chunks table
    and provides similarity search using cosine distance.
    
    Features:
    - LangChain VectorStore interface compatibility
    - Cosine similarity search via pgvector
    - Optional hybrid search (semantic + BM25 full-text)
    - HNSW index support for fast approximate nearest neighbor
    
    Example:
        >>> store = PostgresVectorStore(
        ...     pg_config={"host": "localhost", "port": 5432, "user": "postgres", "password": "...", "dbname": "archi"},
        ...     embedding_function=HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2"),
        ...     collection_name="my_collection",
        ... )
        >>> docs = store.similarity_search("What is machine learning?", k=5)
    """
    
    def __init__(
        self,
        pg_config: Dict[str, Any],
        embedding_function: Embeddings,
        collection_name: str = "default",
        distance_metric: str = "cosine",
        *,
        # Optional: pre-connected cursor (for connection pooling)
        connection: Optional[psycopg2.extensions.connection] = None,
    ):
        """
        Initialize PostgresVectorStore.
        
        Args:
            pg_config: PostgreSQL connection parameters (host, port, user, password, dbname)
            embedding_function: LangChain Embeddings instance for generating vectors
            collection_name: Logical collection name (stored in metadata for filtering)
            distance_metric: Distance metric - 'cosine', 'l2', or 'inner_product'
            connection: Optional pre-existing connection (for pooling)
        """
        self._pg_config = pg_config
        self._embedding_function = embedding_function
        self._collection_name = collection_name
        self._distance_metric = distance_metric
        self._external_connection = connection
        
        # Map distance metric to pgvector operator
        self._distance_ops = {
            "cosine": "<=>",      # Cosine distance (1 - cosine_similarity)
            "l2": "<->",          # Euclidean distance  
            "inner_product": "<#>",  # Negative inner product
        }
        if distance_metric not in self._distance_ops:
            raise ValueError(f"distance_metric must be one of {list(self._distance_ops.keys())}")
        
        self._distance_op = self._distance_ops[distance_metric]
        logger.info(
            "PostgresVectorStore initialized: collection=%s, distance=%s",
            collection_name,
            distance_metric,
        )
    
    @property
    def embeddings(self) -> Optional[Embeddings]:
        """Return the embedding function."""
        return self._embedding_function
    
    def _get_connection(self) -> psycopg2.extensions.connection:
        """Get a database connection."""
        if self._external_connection is not None:
            return self._external_connection
        return psycopg2.connect(**self._pg_config)
    
    def _close_connection(self, conn: psycopg2.extensions.connection) -> None:
        """Close connection if we created it."""
        if self._external_connection is None:
            conn.close()
    
    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
        *,
        ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> List[str]:
        """
        Add texts with embeddings to the vector store.
        
        Args:
            texts: Iterable of text strings to embed and store
            metadatas: Optional list of metadata dicts for each text
            ids: Optional list of IDs (will be generated if not provided)
            **kwargs: Additional arguments (document_id for linking to documents table)
        
        Returns:
            List of IDs for the added texts
        """
        texts_list = list(texts)
        if not texts_list:
            return []
        
        # Generate IDs if not provided
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in texts_list]
        
        # Prepare metadatas
        if metadatas is None:
            metadatas = [{} for _ in texts_list]
        
        # Add collection name to metadata
        for meta in metadatas:
            meta["collection"] = self._collection_name
        
        # Generate embeddings
        logger.debug("Generating embeddings for %d texts", len(texts_list))
        embeddings = self._embedding_function.embed_documents(texts_list)
        
        # Get document_id if provided (for foreign key relationship)
        document_id = kwargs.get("document_id")
        
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                # Insert chunks
                insert_data = []
                for i, (text, embedding, metadata, chunk_id) in enumerate(
                    zip(texts_list, embeddings, metadatas, ids)
                ):
                    # Store the chunk_id in metadata for retrieval
                    metadata["chunk_id"] = chunk_id
                    
                    insert_data.append((
                        document_id,  # May be None if not linked to documents table
                        i,  # chunk_index
                        text,
                        embedding,
                        json.dumps(metadata),
                    ))
                
                # Use execute_values for efficient batch insert
                psycopg2.extras.execute_values(
                    cursor,
                    """
                    INSERT INTO document_chunks (document_id, chunk_index, chunk_text, embedding, metadata)
                    VALUES %s
                    ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                        chunk_text = EXCLUDED.chunk_text,
                        embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata
                    """,
                    insert_data,
                    template="(%s, %s, %s, %s::vector, %s::jsonb)",
                )
                conn.commit()
                logger.debug("Inserted %d chunks", len(insert_data))
        finally:
            self._close_connection(conn)
        
        return ids
    
    def add_documents(
        self,
        documents: List[Document],
        **kwargs: Any,
    ) -> List[str]:
        """
        Add LangChain Documents to the vector store.
        
        Args:
            documents: List of Document objects with page_content and metadata
            **kwargs: Additional arguments passed to add_texts
        
        Returns:
            List of IDs for the added documents
        """
        texts = [doc.page_content for doc in documents]
        metadatas = [doc.metadata for doc in documents]
        return self.add_texts(texts, metadatas=metadatas, **kwargs)
    
    def similarity_search(
        self,
        query: str,
        k: int = 4,
        **kwargs: Any,
    ) -> List[Document]:
        """
        Search for documents most similar to the query.
        
        Args:
            query: Query text to search for
            k: Number of results to return
            **kwargs: Additional filters (filter dict for metadata filtering)
        
        Returns:
            List of Documents ordered by similarity (most similar first)
        """
        docs_and_scores = self.similarity_search_with_score(query, k=k, **kwargs)
        return [doc for doc, _ in docs_and_scores]
    
    def similarity_search_with_score(
        self,
        query: str,
        k: int = 4,
        **kwargs: Any,
    ) -> List[Tuple[Document, float]]:
        """
        Search for documents with similarity scores.
        
        Args:
            query: Query text to search for
            k: Number of results to return
            **kwargs: Additional filters
        
        Returns:
            List of (Document, score) tuples ordered by similarity
        """
        # Generate query embedding
        query_embedding = self._embedding_function.embed_query(query)
        return self.similarity_search_by_vector_with_score(
            query_embedding, k=k, **kwargs
        )
    
    def similarity_search_by_vector(
        self,
        embedding: List[float],
        k: int = 4,
        **kwargs: Any,
    ) -> List[Document]:
        """
        Search by embedding vector.
        
        Args:
            embedding: Query embedding vector
            k: Number of results to return
            **kwargs: Additional filters
        
        Returns:
            List of Documents ordered by similarity
        """
        docs_and_scores = self.similarity_search_by_vector_with_score(
            embedding, k=k, **kwargs
        )
        return [doc for doc, _ in docs_and_scores]
    
    def similarity_search_by_vector_with_score(
        self,
        embedding: List[float],
        k: int = 4,
        **kwargs: Any,
    ) -> List[Tuple[Document, float]]:
        """
        Search by embedding vector with scores.
        
        Args:
            embedding: Query embedding vector
            k: Number of results to return
            **kwargs: filter (dict) - metadata filters, include_deleted (bool)
        
        Returns:
            List of (Document, score) tuples
        """
        metadata_filter = kwargs.get("filter", {})
        include_deleted = kwargs.get("include_deleted", False)
        
        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                # Build query with collection filter
                where_clauses = ["(c.metadata->>'collection' = %s OR c.metadata->>'collection' IS NULL)"]
                params: List[Any] = [self._collection_name]
                
                # Add metadata filters
                for key, value in metadata_filter.items():
                    where_clauses.append(f"c.metadata->>'{key}' = %s")
                    params.append(str(value))
                
                # Filter out deleted documents (if linked to documents table)
                if not include_deleted:
                    where_clauses.append(
                        "(d.id IS NULL OR d.is_deleted = FALSE)"
                    )
                
                where_sql = " AND ".join(where_clauses)
                
                # Format embedding as PostgreSQL array
                embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
                params.insert(0, embedding_str)
                params.append(k)
                
                query = f"""
                    SELECT 
                        c.id,
                        c.chunk_index,
                        c.chunk_text,
                        c.metadata,
                        c.embedding {self._distance_op} %s::vector AS distance,
                        d.resource_hash,
                        d.display_name,
                        d.source_type,
                        d.url
                    FROM document_chunks c
                    LEFT JOIN documents d ON c.document_id = d.id
                    WHERE {where_sql}
                    ORDER BY distance ASC
                    LIMIT %s
                """
                
                cursor.execute(query, params)
                rows = cursor.fetchall()
        finally:
            self._close_connection(conn)
        
        results: List[Tuple[Document, float]] = []
        for row in rows:
            # Merge chunk metadata with document metadata
            metadata = row["metadata"] or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            if row.get("chunk_index") is not None:
                metadata.setdefault("chunk_index", row["chunk_index"])
            if row.get("id") is not None:
                metadata.setdefault("document_chunk_id", row["id"])
            
            # Add document-level metadata
            if row["resource_hash"]:
                metadata["resource_hash"] = row["resource_hash"]
            if row["display_name"]:
                metadata["display_name"] = row["display_name"]
            if row["source_type"]:
                metadata["source_type"] = row["source_type"]
            if row["url"]:
                metadata["url"] = row["url"]
            
            doc = Document(
                page_content=row["chunk_text"],
                metadata=metadata,
            )
            # Convert distance to similarity score (for cosine: 1 - distance)
            score = 1.0 - row["distance"] if self._distance_metric == "cosine" else row["distance"]
            results.append((doc, score))
        
        return results
    
    def hybrid_search(
        self,
        query: str,
        k: int = 4,
        *,
        semantic_weight: float = 0.7,
        bm25_weight: float = 0.3,
        **kwargs: Any,
    ) -> List[Tuple[Document, float]]:
        """
        Hybrid search combining semantic similarity and BM25 full-text search.
        
        Args:
            query: Query text
            k: Number of results to return
            semantic_weight: Weight for semantic similarity (0-1)
            bm25_weight: Weight for BM25 score (0-1)
            **kwargs: Additional filters
        
        Returns:
            List of (Document, combined_score) tuples
        """
        logger.debug("Performing hybrid search: query='%s', k=%d", query, k)

        query_embedding = self._embedding_function.embed_query(query)
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        metadata_filter = kwargs.get("filter", {})
        include_deleted = kwargs.get("include_deleted", False)

        conn = self._get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(
                    """
                    SELECT idx.relname
                    FROM pg_class t
                    JOIN pg_index i ON t.oid = i.indrelid
                    JOIN pg_class idx ON idx.oid = i.indexrelid
                    JOIN pg_am am ON am.oid = idx.relam
                    JOIN pg_namespace n ON n.oid = t.relnamespace
                    WHERE t.relname = 'document_chunks'
                      AND n.nspname = 'public'
                      AND am.amname = 'bm25'
                    LIMIT 1
                    """
                )
                bm25_index_row = cursor.fetchone()
                bm25_index_name = bm25_index_row["relname"] if bm25_index_row else None
                if not bm25_index_name:
                    raise RuntimeError(
                        "Hybrid search requires pg_textsearch BM25 index on document_chunks; none found."
                    )

                where_clauses = ["(c.metadata->>'collection' = %s OR c.metadata->>'collection' IS NULL)"]
                params: List[Any] = [self._collection_name]

                for key, value in metadata_filter.items():
                    where_clauses.append(f"c.metadata->>'{key}' = %s")
                    params.append(str(value))

                if not include_deleted:
                    where_clauses.append("(d.id IS NULL OR d.is_deleted = FALSE)")

                where_sql = " AND ".join(where_clauses)

                # Use pg_textsearch BM25 operator with explicit index target
                bm25_score_expr = f"c.chunk_text <@> to_bm25query(%s, '{bm25_index_name}')"

                query_sql = f"""
                    WITH scored AS (
                        SELECT 
                            c.id,
                            c.chunk_index,
                            c.chunk_text,
                            c.metadata,
                            1.0 - (c.embedding {self._distance_op} %s::vector) AS semantic_score,
                            {bm25_score_expr} AS bm25_score,
                            d.resource_hash,
                            d.display_name,
                            d.source_type,
                            d.url
                        FROM document_chunks c
                        LEFT JOIN documents d ON c.document_id = d.id
                        WHERE {where_sql}
                    )
                    SELECT 
                        *,
                        (semantic_score * %s + COALESCE(bm25_score, 0) * %s) AS combined_score
                    FROM scored
                    ORDER BY combined_score DESC
                    LIMIT %s
                """

                # Params order: embedding, collection (+ any filters), query, semantic_weight, bm25_weight, k
                all_params = [embedding_str] + params + [query, semantic_weight, bm25_weight, k]
                cursor.execute(query_sql, all_params)
                rows = cursor.fetchall()
        finally:
            self._close_connection(conn)

        results: List[Tuple[Document, float]] = []
        # If BM25 returned zero rows, fall back to semantic similarity to avoid empty results
        if not rows:
            return self.similarity_search_with_score(query, k=k, **kwargs)

        for row in rows:
            metadata = row["metadata"] or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            if row.get("chunk_index") is not None:
                metadata.setdefault("chunk_index", row["chunk_index"])
            if row.get("id") is not None:
                metadata.setdefault("document_chunk_id", row["id"])

            if row["resource_hash"]:
                metadata["resource_hash"] = row["resource_hash"]
            if row["display_name"]:
                metadata["display_name"] = row["display_name"]
            if row["source_type"]:
                metadata["source_type"] = row["source_type"]
            if row["url"]:
                metadata["url"] = row["url"]
            
            doc = Document(
                page_content=row["chunk_text"],
                metadata=metadata,
            )
            results.append((doc, row["combined_score"]))
        
        return results
    
    def delete(
        self,
        ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Optional[bool]:
        """
        Delete documents from the vector store.
        
        Args:
            ids: List of chunk IDs (from metadata.chunk_id) to delete
            **kwargs: document_id to delete all chunks for a document
        
        Returns:
            True if deletion was successful
        """
        document_id = kwargs.get("document_id")
        
        if ids is None and document_id is None:
            logger.warning("delete() called with no ids or document_id")
            return False
        
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                if document_id is not None:
                    cursor.execute(
                        "DELETE FROM document_chunks WHERE document_id = %s",
                        (document_id,)
                    )
                elif ids:
                    # Delete by chunk_id in metadata
                    for chunk_id in ids:
                        cursor.execute(
                            "DELETE FROM document_chunks WHERE metadata->>'chunk_id' = %s",
                            (chunk_id,)
                        )
                conn.commit()
                deleted = cursor.rowcount
                logger.debug("Deleted %d chunks", deleted)
        finally:
            self._close_connection(conn)
        
        return True
    
    @classmethod
    def from_texts(
        cls: Type["PostgresVectorStore"],
        texts: List[str],
        embedding: Embeddings,
        metadatas: Optional[List[Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> "PostgresVectorStore":
        """
        Create a PostgresVectorStore from texts.
        
        Args:
            texts: List of texts to add
            embedding: Embeddings instance
            metadatas: Optional metadata for each text
            **kwargs: Must include pg_config
        
        Returns:
            Initialized PostgresVectorStore with texts added
        """
        pg_config = kwargs.pop("pg_config")
        collection_name = kwargs.pop("collection_name", "default")
        distance_metric = kwargs.pop("distance_metric", "cosine")
        
        store = cls(
            pg_config=pg_config,
            embedding_function=embedding,
            collection_name=collection_name,
            distance_metric=distance_metric,
        )
        store.add_texts(texts, metadatas=metadatas, **kwargs)
        return store
    
    def count(self) -> int:
        """Return the number of chunks in this collection."""
        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) FROM document_chunks 
                    WHERE metadata->>'collection' = %s OR metadata->>'collection' IS NULL
                    """,
                    (self._collection_name,)
                )
                result = cursor.fetchone()
                return result[0] if result else 0
        finally:
            self._close_connection(conn)
