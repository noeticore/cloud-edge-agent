"""Retriever — bridges MemoryStore with the RAG Retriever interface."""

from app.core.logger.logger import get_logger
from app.domain.memory.memory import MemoryStore
from app.domain.rag.rag import Document, Retriever, RetrievalResult

logger = get_logger(__name__)


class MemoryRetriever(Retriever):
    """Retrieve relevant documents from any MemoryStore implementation.

    Follows DIP: depends on the abstract MemoryStore, not a concrete backend.
    """

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    async def retrieve(
        self, query: str, top_k: int = 5, filters: dict | None = None
    ) -> list[RetrievalResult]:
        """Retrieve top-k documents by semantic similarity.

        Args:
            query: search query.
            top_k: maximum results to return.
            filters: reserved for future use (e.g. session_id filter).

        Returns:
            List of RetrievalResult with document and relevance score.
        """
        entries = await self._store.search(query, top_k=top_k)
        results = []
        for entry in entries:
            doc = Document(
                content=entry.content,
                metadata=entry.metadata,
                doc_id=entry.entry_id,
            )
            results.append(RetrievalResult(document=doc, score=entry.score))

        logger.info(
            "retriever_search",
            query=query[:80],
            results=len(results),
        )
        return results
