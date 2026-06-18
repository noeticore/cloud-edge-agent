"""Qdrant-backed vector store for long-term memory and RAG."""

import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from app.core.config.settings import VectorStoreSettings
from app.core.exceptions.exceptions import MemoryException
from app.core.logger.logger import get_logger
from app.domain.memory.memory import MemoryEntry, MemoryStore, MemoryType
from app.domain.rag.rag import Embedder

logger = get_logger(__name__)

# Map string distance names to Qdrant Distance enum
_DISTANCE_MAP = {
    "cosine": Distance.COSINE,
    "euclid": Distance.EUCLID,
    "dot": Distance.DOT,
    "manhattan": Distance.MANHATTAN,
}


class QdrantMemoryStore(MemoryStore):
    """Long-term memory backed by Qdrant vector database.

    Supports both local and cloud Qdrant instances.
    Requires an Embedder to compute vectors for add/search operations.
    """

    memory_type = MemoryType.LONG_TERM

    def __init__(
        self,
        embedder: Embedder,
        settings: VectorStoreSettings,
    ) -> None:
        self._embedder = embedder
        self._url = settings.url
        self._api_key = settings.api_key or None
        self._collection = settings.collection
        self._vector_size = settings.vector_size
        self._timeout = settings.timeout
        self._client: AsyncQdrantClient | None = None

    async def _ensure_client(self) -> AsyncQdrantClient:
        """Lazily create the Qdrant client."""
        if self._client is None:
            # Connect to Qdrant (supports both local and cloud)
            if self._api_key:
                # Cloud Qdrant with API key
                self._client = AsyncQdrantClient(
                    url=self._url,
                    api_key=self._api_key,
                    timeout=self._timeout,
                )
                logger.info("qdrant_cloud_connected", url=self._url)
            else:
                # Local Qdrant
                self._client = AsyncQdrantClient(
                    url=self._url,
                    timeout=self._timeout,
                )
                logger.info("qdrant_local_connected", url=self._url)

            # Create collection if it doesn't exist
            try:
                collections = await self._client.get_collections()
                names = [c.name for c in collections.collections]
                if self._collection not in names:
                    await self._client.create_collection(
                        collection_name=self._collection,
                        vectors_config=VectorParams(
                            size=self._vector_size, distance=Distance.COSINE
                        ),
                    )
                    logger.info("qdrant_collection_created", collection=self._collection)
                else:
                    logger.info("qdrant_collection_exists", collection=self._collection)
            except Exception as exc:
                logger.warning("qdrant_collection_check_failed", error=str(exc))
                # Don't fail — collection might already exist or be created later
        return self._client

    async def add(self, entry: MemoryEntry) -> str:
        """Store a memory entry with its embedding.

        Args:
            entry: memory entry to store.

        Returns:
            The point ID (UUID string) assigned to this entry.
        """
        client = await self._ensure_client()
        point_id = entry.entry_id or str(uuid.uuid4())
        vector = await self._embedder.embed(entry.content)
        try:
            await client.upsert(
                collection_name=self._collection,
                points=[
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "content": entry.content,
                            "metadata": entry.metadata,
                            "session_id": entry.session_id,
                        },
                    )
                ],
            )
            logger.info(
                "qdrant_add",
                collection=self._collection,
                point_id=point_id,
                content_len=len(entry.content),
            )
            return point_id
        except Exception as exc:
            raise MemoryException(f"Failed to add memory: {exc}") from exc

    async def search(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        """Search by vector similarity.

        Args:
            query: natural language query to search for.
            top_k: maximum number of results to return.

        Returns:
            List of matching MemoryEntry objects, ordered by relevance.
        """
        client = await self._ensure_client()
        vector = await self._embedder.embed(query)
        try:
            # Use query_points (newer API) with fallback to search
            if hasattr(client, "query_points"):
                # qdrant-client >= 1.18.0
                from qdrant_client.models import QueryRequest

                results = await client.query_points(
                    collection_name=self._collection,
                    query=vector,
                    limit=top_k,
                    with_payload=True,
                )
                hits = results.points
            else:
                # Older qdrant-client versions
                hits = await client.search(
                    collection_name=self._collection,
                    query_vector=vector,
                    limit=top_k,
                    with_payload=True,
                )
            return [
                MemoryEntry(
                    content=hit.payload.get("content", ""),
                    metadata=hit.payload.get("metadata", {}),
                    session_id=hit.payload.get("session_id", ""),
                    entry_id=str(hit.id),
                    score=hit.score,
                )
                for hit in hits
            ]
        except Exception as exc:
            raise MemoryException(f"Failed to search memories: {exc}") from exc

    async def get_recent(self, limit: int = 10) -> list[MemoryEntry]:
        """Get most recent entries (scroll by insertion order)."""
        client = await self._ensure_client()
        try:
            results = await client.scroll(
                collection_name=self._collection,
                limit=limit,
                with_payload=True,
            )
            return [
                MemoryEntry(
                    content=point.payload.get("content", ""),
                    metadata=point.payload.get("metadata", {}),
                    session_id=point.payload.get("session_id", ""),
                    entry_id=str(point.id),
                )
                for point in results[0]
            ]
        except Exception as exc:
            raise MemoryException(f"Failed to get recent memories: {exc}") from exc

    async def clear(self, session_id: str | None = None) -> None:
        """Clear all memories (optionally filter by session)."""
        client = await self._ensure_client()
        try:
            if session_id:
                await client.delete(
                    collection_name=self._collection,
                    points_selector={
                        "filter": {
                            "must": [
                                {"key": "session_id", "match": {"value": session_id}}
                            ]
                        }
                    },
                )
            else:
                await client.delete_collection(self._collection)
                logger.info("qdrant_collection_deleted", collection=self._collection)
        except Exception as exc:
            raise MemoryException(f"Failed to clear memories: {exc}") from exc
