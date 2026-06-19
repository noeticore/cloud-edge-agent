"""Tests for ChatService — conversation storage and context injection."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.memory.memory import ConversationEntry, ConversationStore
from app.services.chat_service import ChatService, _MAX_CONTEXT_MESSAGES


class FakeConversationStore(ConversationStore):
    """In-memory conversation store for testing."""

    def __init__(self) -> None:
        self._entries: list[ConversationEntry] = []

    async def add(self, entry: ConversationEntry) -> str:
        entry.entry_id = str(len(self._entries))
        self._entries.append(entry)
        return entry.entry_id

    async def get_history(
        self, session_id: str, limit: int = 10, content_type: str = "original"
    ) -> list[ConversationEntry]:
        session_entries = [e for e in self._entries if e.session_id == session_id]
        return session_entries[-limit:]

    async def get_context_messages(
        self, session_id: str, limit: int = 10, content_type: str = "original"
    ) -> list[dict[str, str]]:
        entries = await self.get_history(session_id, limit, content_type)
        messages = []
        for entry in entries:
            if content_type == "sanitized" and entry.has_sensitive_data:
                content = entry.sanitized_content or entry.original_content
            elif content_type == "sanitized" and entry.restored_content:
                content = entry.restored_content
            else:
                content = entry.original_content
            messages.append({"role": entry.role, "content": content})
        return messages

    async def search(
        self, query: str, session_id: str | None = None, top_k: int = 5
    ) -> list[ConversationEntry]:
        return self._entries[:top_k]

    async def clear(self, session_id: str | None = None) -> None:
        if session_id:
            self._entries = [e for e in self._entries if e.session_id != session_id]
        else:
            self._entries.clear()


def _make_chat_service(
    conversation_store: FakeConversationStore | None = None,
) -> tuple[ChatService, MagicMock, MagicMock, FakeConversationStore]:
    """Create ChatService with mocked orchestrator and session store."""
    from app.services.agent_orchestrator import OrchestratorResult
    from app.domain.privacy.policy import CollaborateMode, ComplexityLevel, RoutingResult
    from app.domain.privacy.privacy import PrivacyDetection, PrivacyLevel

    orchestrator = MagicMock()
    orchestrator.process = AsyncMock(
        return_value=OrchestratorResult(
            answer="test answer",
            mode=CollaborateMode.DIRECT_LOCAL,
            routing=RoutingResult(
                decision=MagicMock(),
                mode=CollaborateMode.DIRECT_LOCAL,
                privacy_level=PrivacyLevel.S1,
                complexity=ComplexityLevel.L1,
                reason="test",
            ),
            privacy_detection=PrivacyDetection(
                level=PrivacyLevel.S1,
                confidence=1.0,
                reason="test",
            ),
            latency_ms=100.0,
            sanitized_query="test answer",
            restore_mapping={},
        )
    )

    session_store = MagicMock()
    session_store.get.return_value = MagicMock()  # session exists
    session_store.create = MagicMock()
    session_store.add_message = MagicMock()

    conv_store = conversation_store or FakeConversationStore()

    service = ChatService(
        orchestrator=orchestrator,
        session_store=session_store,
        conversation_store=conv_store,
        rag_pipeline=None,
    )
    return service, orchestrator, session_store, conv_store


class TestEnrichQuery:
    """Tests for ChatService._enrich_query()."""

    def test_no_context_returns_original(self) -> None:
        result = ChatService._enrich_query("hello", [], "")
        assert result == "hello"

    def test_with_context_prepends_history(self) -> None:
        context = [
            {"role": "user", "content": "prev question"},
            {"role": "assistant", "content": "prev answer"},
        ]
        result = ChatService._enrich_query("new question", context, "")

        assert "Relevant conversation history:" in result
        assert "[user] prev question" in result
        assert "[assistant] prev answer" in result
        assert "Current question: new question" in result

    def test_context_without_role_metadata(self) -> None:
        context = [{"role": "unknown", "content": "some memory"}]
        result = ChatService._enrich_query("query", context, "")

        assert "[unknown] some memory" in result

    def test_with_rag_context(self) -> None:
        rag = "- Document about AI\n- Document about ML"
        result = ChatService._enrich_query("query", [], rag)

        assert "Relevant documents:" in result
        assert "Document about AI" in result
        assert "Current question: query" in result

    def test_with_both_context_and_rag(self) -> None:
        context = [{"role": "user", "content": "hello"}]
        rag = "- Some document"
        result = ChatService._enrich_query("query", context, rag)

        assert "Relevant conversation history:" in result
        assert "Relevant documents:" in result
        assert "Current question: query" in result


class TestChatFlow:
    """Tests for the full ChatService.chat() flow."""

    @pytest.mark.asyncio
    async def test_chat_returns_response(self) -> None:
        """Test basic chat flow."""
        service, orchestrator, _, _ = _make_chat_service()

        result = await service.chat("hello", session_id="s1")

        assert result.answer == "test answer"
        assert result.session_id == "s1"
        assert result.privacy_level == "S1"
        orchestrator.process.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_stores_conversation(self) -> None:
        """Test that chat stores entries in conversation store."""
        store = FakeConversationStore()
        service, _, _, _ = _make_chat_service(conversation_store=store)

        await service.chat("hello", session_id="s1")

        entries = await store.get_history("s1", limit=10)
        assert len(entries) == 2  # user + assistant
        assert entries[0].role == "user"
        assert entries[0].original_content == "hello"
        assert entries[1].role == "assistant"
        assert entries[1].original_content == "test answer"

    @pytest.mark.asyncio
    async def test_chat_enriches_query_with_context(self) -> None:
        """Test that context is used to enrich the query."""
        store = FakeConversationStore()
        service, orchestrator, _, _ = _make_chat_service(conversation_store=store)

        # First message
        await service.chat("my name is Alice", session_id="s1")

        # Second message - should have context from first
        await service.chat("what's my name?", session_id="s1")

        # Check that the second call had enriched context
        call_args = orchestrator.process.call_args
        enriched_query = call_args.kwargs["query"]
        assert "what's my name?" in enriched_query

    @pytest.mark.asyncio
    async def test_chat_sensitive_stores_sanitized_content(self) -> None:
        """Test that sensitive queries store sanitized content."""
        from app.services.agent_orchestrator import OrchestratorResult
        from app.domain.privacy.policy import CollaborateMode, ComplexityLevel, RoutingResult
        from app.domain.privacy.privacy import PrivacyDetection, PrivacyLevel

        store = FakeConversationStore()
        service, orchestrator, _, _ = _make_chat_service(conversation_store=store)

        # Override orchestrator to return S2 privacy
        orchestrator.process = AsyncMock(
            return_value=OrchestratorResult(
                answer="I cannot provide that info",
                mode=CollaborateMode.SANITIZE_CLOUD,
                routing=RoutingResult(
                    decision=MagicMock(),
                    mode=CollaborateMode.SANITIZE_CLOUD,
                    privacy_level=PrivacyLevel.S2,
                    complexity=ComplexityLevel.L3,
                    reason="test",
                ),
                privacy_detection=PrivacyDetection(
                    level=PrivacyLevel.S2,
                    confidence=0.9,
                    reason="phone detected",
                ),
                latency_ms=200.0,
                sanitized_query="my phone is [REDACTED:PHONE:abc123]",
                restore_mapping={"[REDACTED:PHONE:abc123]": "13812345678"},
            )
        )

        await service.chat("my phone is 13812345678", session_id="s1")

        entries = await store.get_history("s1", limit=10)
        user_entry = entries[0]
        assert user_entry.has_sensitive_data is True
        assert user_entry.sanitized_content == "my phone is [REDACTED:PHONE:abc123]"
        assert user_entry.privacy_level == "S2"
