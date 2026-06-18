"""Tests for MemoryRetriever — vector retrieval via MemoryStore."""

import pytest

from app.domain.memory.memory import MemoryEntry, MemoryStore, MemoryType
from app.infrastructure.rag.retriever import MemoryRetriever


class FakeMemoryStore(MemoryStore):
    """Stub MemoryStore returning pre-configured entries."""

    memory_type = MemoryType.SHORT_TERM

    def __init__(self, entries: list[MemoryEntry]) -> None:
        self._entries = entries

    async def add(self, entry: MemoryEntry) -> str:
        raise NotImplementedError

    async def search(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        return self._entries[:top_k]

    async def get_recent(self, limit: int = 10) -> list[MemoryEntry]:
        return self._entries[:limit]

    async def clear(self, session_id: str | None = None) -> None:
        pass


class TestMemoryRetriever:
    """Unit tests for MemoryRetriever."""

    @pytest.mark.asyncio
    async def test_returns_retrieval_results_with_scores(self) -> None:
        entries = [
            MemoryEntry(content="doc one", metadata={"k": "1"}, entry_id="e1", score=0.95),
            MemoryEntry(content="doc two", metadata={"k": "2"}, entry_id="e2", score=0.72),
        ]
        retriever = MemoryRetriever(FakeMemoryStore(entries))

        results = await retriever.retrieve("query", top_k=5)

        assert len(results) == 2
        assert results[0].document.content == "doc one"
        assert results[0].document.doc_id == "e1"
        assert results[0].score == 0.95
        assert results[1].score == 0.72

    @pytest.mark.asyncio
    async def test_respects_top_k(self) -> None:
        entries = [
            MemoryEntry(content=f"doc {i}", entry_id=f"e{i}") for i in range(10)
        ]
        retriever = MemoryRetriever(FakeMemoryStore(entries))

        results = await retriever.retrieve("query", top_k=3)

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_empty_results(self) -> None:
        retriever = MemoryRetriever(FakeMemoryStore([]))

        results = await retriever.retrieve("query")

        assert results == []

    @pytest.mark.asyncio
    async def test_metadata_forwarded(self) -> None:
        entries = [
            MemoryEntry(
                content="test", metadata={"source": "wiki"}, entry_id="e1", score=0.8
            )
        ]
        retriever = MemoryRetriever(FakeMemoryStore(entries))

        results = await retriever.retrieve("query")

        assert results[0].document.metadata == {"source": "wiki"}
