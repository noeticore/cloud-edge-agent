"""RAG pipeline — chains Chunker → Embedder → VectorStore → Retriever → Reranker.

Provides two main operations:
  - ingest(): text → chunk → embed → store
  - retrieve(): query → embed → vector search → rerank → return
"""

import uuid

from app.core.logger.logger import get_logger
from app.domain.memory.memory import MemoryEntry, MemoryStore
from app.domain.rag.rag import (
    Chunker,
    Document,
    Embedder,
    Reranker,
    Retriever,
    RetrievalResult,
)

logger = get_logger(__name__)


class RAGPipeline:
    """End-to-end RAG pipeline that chains chunking, embedding, storage,
    retrieval, and reranking.

    Follows DIP: depends on abstract interfaces (Chunker, Embedder, etc.),
    not concrete implementations.
    """

    def __init__(
        self,
        chunker: Chunker,
        vector_store: MemoryStore,
        retriever: Retriever,
        reranker: Reranker | None = None,
    ) -> None:
        """Initialize the RAG pipeline.

        Args:
            chunker: splits documents into chunks.
            vector_store: stores document chunks with embeddings.
            retriever: retrieves relevant chunks by semantic similarity.
            reranker: optional reranker for better relevance.
        """
        self._chunker = chunker
        self._store = vector_store
        self._retriever = retriever
        self._reranker = reranker

    async def ingest(
        self,
        text: str,
        metadata: dict | None = None,
        doc_id: str | None = None,
    ) -> list[str]:
        """Ingest a document: chunk → embed → store.

        Args:
            text: raw document text.
            metadata: optional metadata (source, title, etc.).
            doc_id: optional document ID (auto-generated if not provided).

        Returns:
            List of chunk IDs that were stored.
        """
        if not text.strip():
            logger.warning("ingest_empty_text")
            return []

        doc_id = doc_id or uuid.uuid4().hex[:12]
        document = Document(
            content=text,
            metadata=metadata or {},
            doc_id=doc_id,
        )

        # Chunk
        chunks = self._chunker.chunk(document)
        logger.info(
            "rag_ingest_chunked",
            doc_id=doc_id,
            chunk_count=len(chunks),
        )

        # Store each chunk (embedder is called inside the store)
        chunk_ids: list[str] = []
        for chunk in chunks:
            entry = MemoryEntry(
                content=chunk.content,
                metadata=chunk.metadata,
                session_id=doc_id,
            )
            entry_id = await self._store.add(entry)
            chunk_ids.append(entry_id)

        logger.info(
            "rag_ingest_complete",
            doc_id=doc_id,
            stored_count=len(chunk_ids),
        )
        return chunk_ids

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        rerank_top_k: int = 3,
    ) -> list[RetrievalResult]:
        """Retrieve relevant documents for a query.

        Args:
            query: search query.
            top_k: number of candidates to retrieve from vector store.
            rerank_top_k: number of results after reranking.

        Returns:
            List of RetrievalResult with document and relevance score.
        """
        # Retrieve candidates
        candidates = await self._retriever.retrieve(query, top_k=top_k)
        logger.info(
            "rag_retrieve_candidates",
            query=query,
            candidate_count=len(candidates),
        )

        # Rerank if available
        if self._reranker and candidates:
            results = await self._reranker.rerank(
                query, candidates, top_k=rerank_top_k
            )
        else:
            results = candidates[:rerank_top_k]

        logger.info(
            "rag_retrieve_complete",
            query=query,
            result_count=len(results),
        )
        return results
