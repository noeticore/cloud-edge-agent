"""Tests for ChatService — memory retrieval and context injection."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.memory.memory import MemoryEntry, MemoryStore, MemoryType
from app.services.chat_service import ChatService, _MAX_CONTEXT_MESSAGES


class FakeShortTermMemory(MemoryStore):
    """In-memory short-term store for testing."""

    memory_type = MemoryType.SHORT_TERM

    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []

    async def add(self, entry: MemoryEntry) -> str:
        entry.entry_id = str(len(self._entries))
        self._entries.append(entry)
        return entry.entry_id

    async def search(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        return self._entries[-top_k:]

    async def get_recent(self, limit: int = 10) -> list[MemoryEntry]:
        return self._entries[-limit:]

    async def clear(self, session_id: str | None = None) -> None:
        self._entries.clear()


class FakeLongTermMemory(MemoryStore):
    """Stub long-term store returning pre-configured entries."""

    memory_type = MemoryType.LONG_TERM

    def __init__(self, entries: list[MemoryEntry] | None = None) -> None:
        self._entries = entries or []
        self.added: list[MemoryEntry] = []

    async def add(self, entry: MemoryEntry) -> str:
        self.added.append(entry)
        return "lt-0"

    async def search(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        return self._entries[:top_k]

    async def get_recent(self, limit: int = 10) -> list[MemoryEntry]:
        return self._entries[:limit]

    async def clear(self, session_id: str | None = None) -> None:
        pass


def _make_chat_service(
    short_term: MemoryStore | None = None,
    long_term: MemoryStore | None = None,
) -> tuple[ChatService, MagicMock, MagicMock, MagicMock]:
    """Create ChatService with mocked orchestrator and session store."""
    orchestrator = MagicMock()
    orchestrator.process = AsyncMock(
        return_value=MagicMock(
            answer="test answer",
            mode=MagicMock(value="direct_local"),
            routing=MagicMock(privacy_level=MagicMock(value="S1"), complexity=MagicMock(value=1)),
            latency_ms=100.0,
        )
    )

    session_store = MagicMock()
    session_store.get.return_value = MagicMock()  # session exists
    session_store.add_message = MagicMock()

    budget_tracker = MagicMock()
    budget_tracker.get_remaining.return_value = 7.0

    st = short_term or FakeShortTermMemory()

    service = ChatService(
        orchestrator=orchestrator,
        session_store=session_store,
        short_term_memory=st,
        budget_tracker=budget_tracker,
        long_term_memory=long_term,
    )
    return service, orchestrator, session_store, budget_tracker


class TestEnrichQuery:
    """Tests for ChatService._enrich_query()."""

    def test_no_context_returns_original(self) -> None:
        result = ChatService._enrich_query("hello", [])
        assert result == "hello"

    def test_with_context_prepends_history(self) -> None:
        context = [
            MemoryEntry(content="prev question", metadata={"role": "user"}),
            MemoryEntry(content="prev answer", metadata={"role": "assistant"}),
        ]
        result = ChatService._enrich_query("new question", context)

        assert "Relevant conversation history:" in result
        assert "[user] prev question" in result
        assert "[assistant] prev answer" in result
        assert "Current question: new question" in result

    def test_context_without_role_metadata(self) -> None:
        context = [MemoryEntry(content="some memory", metadata={})]
        result = ChatService._enrich_query("query", context)

        assert "[unknown] some memory" in result


class TestRetrieveContext:
    """Tests for ChatService._retrieve_context()."""

    @pytest.mark.asyncio
    async def test_retrieves_from_short_term(self) -> None:
        st = FakeShortTermMemory()
        await st.add(MemoryEntry(content="msg1", session_id="s1", metadata={"role": "user"}))
        await st.add(MemoryEntry(content="msg2", session_id="s1", metadata={"role": "assistant"}))

        service, _, _, _ = _make_chat_service(short_term=st)
        context = await service._retrieve_context("query", "s1")

        assert len(context) >= 2
        assert any(e.content == "msg1" for e in context)

    @pytest.mark.asyncio
    async def test_retrieves_from_long_term(self) -> None:
        lt = FakeLongTermMemory(
            entries=[MemoryEntry(content="past conversation", session_id="other", score=0.9)]
        )

        service, _, _, _ = _make_chat_service(long_term=lt)
        context = await service._retrieve_context("query", "s1")

        assert any(e.content == "past conversation" for e in context)

    @pytest.mark.asyncio
    async def test_long_term_failure_graceful(self) -> None:
        """If long-term search fails, short-term context is still returned."""

        class BrokenLongTerm(FakeLongTermMemory):
            async def search(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
                raise RuntimeError("Qdrant down")

        st = FakeShortTermMemory()
        await st.add(MemoryEntry(content="msg", session_id="s1"))

        service, _, _, _ = _make_chat_service(short_term=st, long_term=BrokenLongTerm())
        context = await service._retrieve_context("query", "s1")

        assert any(e.content == "msg" for e in context)

    @pytest.mark.asyncio
    async def test_no_long_term_returns_short_term_only(self) -> None:
        st = FakeShortTermMemory()
        await st.add(MemoryEntry(content="msg", session_id="s1"))

        service, _, _, _ = _make_chat_service(short_term=st, long_term=None)
        context = await service._retrieve_context("query", "s1")

        assert len(context) >= 1


class TestChatFlow:
    """Tests for the full ChatService.chat() flow."""

    @pytest.mark.asyncio
    async def test_chat_stores_in_long_term(self) -> None:
        lt = FakeLongTermMemory()
        service, _, _, _ = _make_chat_service(long_term=lt)

        await service.chat("hello", session_id="s1")

        assert len(lt.added) == 1
        assert "hello" in lt.added[0].content
        assert "test answer" in lt.added[0].content

    @pytest.mark.asyncio
    async def test_chat_without_long_term(self) -> None:
        service, orchestrator, _, _ = _make_chat_service(long_term=None)

        result = await service.chat("hello", session_id="s1")

        assert result.answer == "test answer"
        assert result.session_id == "s1"
        orchestrator.process.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_enriches_query_with_context(self) -> None:
        st = FakeShortTermMemory()
        await st.add(MemoryEntry(
            content="my name is Alice", session_id="s1", metadata={"role": "user"}
        ))

        service, orchestrator, _, _ = _make_chat_service(short_term=st)

        await service.chat("what's my name?", session_id="s1")

        call_args = orchestrator.process.call_args
        enriched_query = call_args.kwargs["query"]
        assert "my name is Alice" in enriched_query
        assert "what's my name?" in enriched_query
