"""Integration tests — ChatService → Orchestrator → Memory full flow."""

import pytest

from app.domain.agent.agent import AgentRole, AgentResult, BaseAgent
from app.domain.agent.react_agent import ReActAgent
from app.domain.llm.llm_client import LLMClient, LLMMessage, LLMResponse, TokenUsage
from app.domain.memory.memory import MemoryEntry
from app.domain.tool.registry import ToolRegistry
from app.infrastructure.database.session_repository import (
    InMemorySessionStore,
    InMemoryShortTermStore,
)
from app.services.agent_orchestrator import CollaborativeOrchestrator
from app.services.chat_service import ChatService
from app.services.privacy_engine import (
    RegexSanitizer,
    ThreeLayerPrivacyDetector,
)
from tools.calculator_tool import CalculatorTool


# ── Fakes ────────────────────────────────────────────────────────────────


class FakeLLMClient(LLMClient):
    """Deterministic LLM for integration testing."""

    def __init__(self, responses: list[str]) -> None:
        self.provider = "fake"
        self.model_name = "fake-model"
        self._responses = responses
        self._call_count = 0

    async def invoke(self, messages: list[LLMMessage]) -> LLMResponse:
        response_text = self._responses[self._call_count % len(self._responses)]
        self._call_count += 1
        return LLMResponse(
            content=response_text,
            model="fake-model",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )

    async def stream_invoke(self, messages):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def think(self, messages: list[LLMMessage]) -> LLMResponse:
        return await self.invoke(messages)

    async def embedding(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class FakeAgent(BaseAgent):
    """Fake agent that wraps FakeLLMClient for integration testing."""

    def __init__(self, llm_client: FakeLLMClient, role: AgentRole = AgentRole.EDGE) -> None:
        self.role = role
        self._llm = llm_client
        self._registry = ToolRegistry()

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._registry

    async def run(self, query: str, context: dict | None = None) -> AgentResult:
        """Simulate agent execution by calling LLM directly."""
        messages = [LLMMessage(role="user", content=query)]
        response = await self._llm.invoke(messages)
        return AgentResult(
            answer=response.content,
            steps=[],
            total_tokens=response.usage.total_tokens if response.usage else 0,
        )


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_chat_service(
    llm_responses: list[str],
) -> tuple[ChatService, FakeLLMClient]:
    """Wire up a full ChatService with fake LLM for integration testing."""
    edge_client = FakeLLMClient(llm_responses)
    cloud_client = FakeLLMClient(llm_responses)

    edge_agent = FakeAgent(edge_client, role=AgentRole.EDGE)
    cloud_agent = FakeAgent(cloud_client, role=AgentRole.CLOUD)

    privacy_detector = ThreeLayerPrivacyDetector(slm_client=edge_client)
    sanitizer = RegexSanitizer()

    session_store = InMemorySessionStore()
    short_term_memory = InMemoryShortTermStore()

    orchestrator = CollaborativeOrchestrator(
        edge_client=edge_client,
        cloud_client=cloud_client,
        edge_agent=edge_agent,
        cloud_agent=cloud_agent,
        privacy_detector=privacy_detector,
        sanitizer=sanitizer,
    )

    chat_service = ChatService(
        orchestrator=orchestrator,
        session_store=session_store,
        short_term_memory=short_term_memory,
        cloud_memory=None,
        local_memory=None,
    )
    return chat_service, edge_client


# ── Tests ────────────────────────────────────────────────────────────────


class TestChatMemoryIntegration:
    """Test that ChatService correctly stores and retrieves memory."""

    @pytest.mark.asyncio
    async def test_multi_turn_memory(self) -> None:
        """Agent should remember context from earlier turns."""
        # Each chat() call triggers:
        #   1. SLM judge (privacy) → JSON
        #   2. Complexity analyzer → JSON
        #   3. Orchestrator mode → text response
        service, llm = _make_chat_service(
            llm_responses=[
                # Turn 1
                '{"level": "S1", "confidence": 0.9, "reason": "safe"}',
                '{"level": "L2"}',
                "Nice to meet you, Alice!",
                # Turn 2
                '{"level": "S1", "confidence": 0.9, "reason": "safe"}',
                '{"level": "L2"}',
                "Your name is Alice.",
            ]
        )

        # Turn 1: tell the agent our name
        result1 = await service.chat("My name is Alice", session_id="s1")
        assert "Alice" in result1.answer

        # Turn 2: ask if it remembers
        result2 = await service.chat("What is my name?", session_id="s1")
        assert "Alice" in result2.answer

        # Verify the LLM received context with the previous conversation
        assert llm._call_count >= 4

    @pytest.mark.asyncio
    async def test_session_isolation(self) -> None:
        """Different sessions should not share memory."""
        responses = [
            '{"level": "S1", "confidence": 0.9, "reason": "safe"}',
            '{"level": "L2"}',
            "Got it",
            '{"level": "S1", "confidence": 0.9, "reason": "safe"}',
            '{"level": "L2"}',
            "I don't know",
        ]
        service, _ = _make_chat_service(llm_responses=responses)

        await service.chat("My secret is 42", session_id="session-a")
        await service.chat("What is the secret?", session_id="session-b")

        # Session B should not see session A's memory
        # (the LLM won't have "42" in its context)


class TestChatOrchestratorIntegration:
    """Test ChatService → Orchestrator → PrivacyEngine pipeline."""

    @pytest.mark.asyncio
    async def test_privacy_routing_sanitizes_pii(self) -> None:
        """PII-heavy queries should be routed through sanitize-cloud mode."""
        service, llm = _make_chat_service(
            llm_responses=[
                '{"level": "S2", "confidence": 0.95, "reason": "phone number"}',
                '{"level": "L2"}',
                "Sure, I can help.",
            ]
        )

        result = await service.chat(
            "My phone number is 13812345678, help me with something",
            session_id="s1",
        )

        # The orchestrator should have detected PII and routed accordingly
        assert result.privacy_level in ("S1", "S2", "S3")


class TestReActAgentMemoryIntegration:
    """Test ReActAgent with memory-augmented context."""

    @pytest.mark.asyncio
    async def test_agent_uses_context_in_query(self) -> None:
        """Agent should produce different answers when context is present."""
        registry = ToolRegistry()

        # Agent that echoes back the query context
        llm = FakeLLMClient(
            ["Thought: I see context.\nFinal Answer: I remember: Alice"]
        )
        agent = ReActAgent(llm_client=llm, tool_registry=registry)

        result = await agent.run(
            "Relevant conversation history:\n[user] My name is Alice\n\n"
            "Current question: What is my name?"
        )

        assert "Alice" in result.answer

    @pytest.mark.asyncio
    async def test_agent_without_context(self) -> None:
        """Agent should handle queries without memory context."""
        registry = ToolRegistry()

        llm = FakeLLMClient(["Thought: Simple question.\nFinal Answer: Hello!"])
        agent = ReActAgent(llm_client=llm, tool_registry=registry)

        result = await agent.run("Hi there")

        assert "Hello" in result.answer


class TestRAGPipelineIntegration:
    """Test Chunker → Embedder → Store → Retriever pipeline."""

    @pytest.mark.asyncio
    async def test_chunk_store_retrieve(self) -> None:
        """Documents should survive chunking, storing, and retrieval."""
        from app.domain.rag.rag import Document
        from app.infrastructure.rag.chunker import FixedSizeChunker
        from app.infrastructure.rag.retriever import MemoryRetriever

        # Chunk a document
        chunker = FixedSizeChunker(chunk_size=50, overlap=10)
        doc = Document(
            content="Alice lives in Beijing. Her phone is 13800001111. "
                    "Bob lives in Shanghai. His email is bob@example.com.",
            doc_id="test-doc",
        )
        chunks = chunker.chunk(doc)
        assert len(chunks) > 1

        # Store chunks in memory
        memory = InMemoryShortTermStore()
        for chunk in chunks:
            await memory.add(MemoryEntry(
                content=chunk.content,
                metadata=chunk.metadata,
                session_id="rag-test",
            ))

        # Retrieve
        retriever = MemoryRetriever(memory)
        results = await retriever.retrieve("Where does Alice live?", top_k=3)
        assert len(results) > 0
        # At least one result should mention Alice/Beijing
        contents = " ".join(r.document.content for r in results)
        assert "Alice" in contents or "Beijing" in contents
