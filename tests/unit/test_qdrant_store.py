"""Tests for QdrantMemoryStore — vector-backed long-term memory."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config.settings import VectorStoreSettings
from app.core.exceptions.exceptions import MemoryException
from app.domain.memory.memory import MemoryEntry
from app.infrastructure.vectorstore.qdrant_store import QdrantMemoryStore


class FakeEmbedder:
    """Stub embedder returning a fixed vector."""

    async def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3]] * len(texts)


def _make_settings() -> VectorStoreSettings:
    """Create test vector store settings."""
    return VectorStoreSettings(
        provider="qdrant",
        url="http://localhost:6333",
        api_key="",
        collection="test_collection",
        vector_size=3,
    )


def _make_store() -> tuple[QdrantMemoryStore, AsyncMock]:
    """Create a QdrantMemoryStore with a mocked Qdrant client."""
    embedder = FakeEmbedder()
    settings = _make_settings()
    store = QdrantMemoryStore(
        embedder=embedder,
        settings=settings,
    )
    mock_client = AsyncMock()
    # Simulate collection already exists
    collections_mock = MagicMock()
    collections_mock.collections = [MagicMock(name="test_collection")]
    mock_client.get_collections.return_value = collections_mock
    store._client = mock_client
    return store, mock_client


class TestQdrantMemoryStoreAdd:
    """Tests for QdrantMemoryStore.add()."""

    @pytest.mark.asyncio
    async def test_add_returns_point_id(self) -> None:
        store, mock_client = _make_store()

        entry = MemoryEntry(content="hello", metadata={"k": "v"}, session_id="s1")
        point_id = await store.add(entry)

        assert isinstance(point_id, str)
        assert len(point_id) > 0
        mock_client.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_uses_provided_entry_id(self) -> None:
        store, mock_client = _make_store()

        entry = MemoryEntry(content="hello", entry_id="custom-id")
        point_id = await store.add(entry)

        assert point_id == "custom-id"

    @pytest.mark.asyncio
    async def test_add_passes_vector_and_payload(self) -> None:
        store, mock_client = _make_store()

        entry = MemoryEntry(content="test content", metadata={"a": 1}, session_id="s1")
        await store.add(entry)

        call_args = mock_client.upsert.call_args
        points = call_args.kwargs["points"]
        assert len(points) == 1
        point = points[0]
        assert point.vector == [0.1, 0.2, 0.3]
        assert point.payload["content"] == "test content"
        assert point.payload["metadata"] == {"a": 1}
        assert point.payload["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_add_wraps_error_as_memory_exception(self) -> None:
        store, mock_client = _make_store()
        mock_client.upsert.side_effect = RuntimeError("connection lost")

        entry = MemoryEntry(content="hello")
        with pytest.raises(MemoryException, match="Failed to add memory"):
            await store.add(entry)


class TestQdrantMemoryStoreSearch:
    """Tests for QdrantMemoryStore.search()."""

    @pytest.mark.asyncio
    async def test_search_returns_memory_entries_with_scores(self) -> None:
        store, mock_client = _make_store()

        hit = MagicMock()
        hit.id = "hit-1"
        hit.score = 0.87
        hit.payload = {
            "content": "found it",
            "metadata": {"source": "test"},
            "session_id": "s1",
        }
        # Mock query_points (newer API)
        mock_result = MagicMock()
        mock_result.points = [hit]
        mock_client.query_points.return_value = mock_result

        results = await store.search("query", top_k=3)

        assert len(results) == 1
        assert results[0].content == "found it"
        assert results[0].metadata == {"source": "test"}
        assert results[0].session_id == "s1"
        assert results[0].entry_id == "hit-1"
        assert results[0].score == 0.87

    @pytest.mark.asyncio
    async def test_search_passes_vector_and_top_k(self) -> None:
        store, mock_client = _make_store()
        mock_result = MagicMock()
        mock_result.points = []
        mock_client.query_points.return_value = mock_result

        await store.search("test query", top_k=10)

        call_args = mock_client.query_points.call_args
        assert call_args.kwargs["query"] == [0.1, 0.2, 0.3]
        assert call_args.kwargs["limit"] == 10

    @pytest.mark.asyncio
    async def test_search_empty_results(self) -> None:
        store, mock_client = _make_store()
        mock_result = MagicMock()
        mock_result.points = []
        mock_client.query_points.return_value = mock_result

        results = await store.search("nothing")

        assert results == []

    @pytest.mark.asyncio
    async def test_search_wraps_error_as_memory_exception(self) -> None:
        store, mock_client = _make_store()
        mock_client.query_points.side_effect = RuntimeError("connection lost")

        with pytest.raises(MemoryException, match="Failed to search memories"):
            await store.search("query")
