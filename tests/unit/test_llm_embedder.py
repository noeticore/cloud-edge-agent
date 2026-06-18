"""Tests for LLMEmbedder — Embedder backed by LLMClient.embedding()."""

import pytest

from app.infrastructure.rag.llm_embedder import LLMEmbedder


class FakeEmbeddingClient:
    """Minimal stub that satisfies LLMClient.embedding() contract."""

    def __init__(self, vectors: list[list[float]] | None = None) -> None:
        self._vectors = vectors or [[0.1, 0.2, 0.3]]
        self._call_count = 0

    async def embedding(self, text: str) -> list[float]:
        vec = self._vectors[self._call_count % len(self._vectors)]
        self._call_count += 1
        return vec


class TestLLMEmbedder:
    """Unit tests for LLMEmbedder."""

    @pytest.mark.asyncio
    async def test_embed_single_text(self) -> None:
        client = FakeEmbeddingClient([[0.5, 0.6, 0.7]])
        embedder = LLMEmbedder(client)

        vector = await embedder.embed("hello world")

        assert vector == [0.5, 0.6, 0.7]
        assert client._call_count == 1

    @pytest.mark.asyncio
    async def test_embed_batch(self) -> None:
        client = FakeEmbeddingClient([[1.0], [2.0], [3.0]])
        embedder = LLMEmbedder(client)

        vectors = await embedder.embed_batch(["a", "b", "c"])

        assert vectors == [[1.0], [2.0], [3.0]]
        assert client._call_count == 3

    @pytest.mark.asyncio
    async def test_embed_batch_empty(self) -> None:
        client = FakeEmbeddingClient()
        embedder = LLMEmbedder(client)

        vectors = await embedder.embed_batch([])

        assert vectors == []
        assert client._call_count == 0

    @pytest.mark.asyncio
    async def test_embed_failure_propagates(self) -> None:
        """When the underlying LLM raises, the exception propagates uncaught."""

        class FailingClient:
            async def embedding(self, text: str) -> list[float]:
                raise RuntimeError("embedding service down")

        embedder = LLMEmbedder(FailingClient())

        with pytest.raises(RuntimeError, match="embedding service down"):
            await embedder.embed("test")
