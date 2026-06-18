"""Chat service — business orchestration for the chat endpoint.

Flow: Request → Session → Memory Retrieval → Orchestrator → Memory Store → Response
"""

from dataclasses import dataclass

from app.core.logger.logger import get_logger
from app.domain.memory.memory import MemoryEntry, MemoryStore
from app.infrastructure.database.session_repository import InMemorySessionStore
from app.services.agent_orchestrator import (
    CollaborativeOrchestrator,
    OrchestratorResult,
)

logger = get_logger(__name__)

# Maximum number of recent messages to inject as context
_MAX_CONTEXT_MESSAGES = 5


@dataclass
class ChatResponse:
    """Response from the chat service."""

    answer: str
    session_id: str
    mode: str
    privacy_level: str
    complexity: int
    latency_ms: float
    budget_remaining: float


class ChatService:
    """Orchestrates a chat request through the full pipeline."""

    def __init__(
        self,
        orchestrator: CollaborativeOrchestrator,
        session_store: InMemorySessionStore,
        short_term_memory: MemoryStore,
        budget_tracker,  # PrivacyBudgetTracker
        long_term_memory: MemoryStore | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._sessions = session_store
        self._short_term = short_term_memory
        self._long_term = long_term_memory
        self._budget = budget_tracker

    async def chat(
        self, query: str, session_id: str | None = None
    ) -> ChatResponse:
        """Process a chat message end-to-end.

        1. Ensure session exists
        2. Retrieve relevant memory context
        3. Enrich query with context
        4. Run orchestrator (privacy → complexity → route → execute)
        5. Store user message and response in memory
        6. Return structured response
        """
        # Ensure session
        if session_id is None:
            import uuid

            session_id = uuid.uuid4().hex[:12]
        if self._sessions.get(session_id) is None:
            self._sessions.create(session_id)

        # Retrieve memory context
        context = await self._retrieve_context(query, session_id)
        enriched_query = self._enrich_query(query, context)

        # Run orchestrator
        result: OrchestratorResult = await self._orchestrator.process(
            query=enriched_query, session_id=session_id
        )

        # Store user message in memory
        self._sessions.add_message(session_id, "user", query)
        await self._short_term.add(
            MemoryEntry(content=query, session_id=session_id, metadata={"role": "user"})
        )

        # Store assistant response in memory
        self._sessions.add_message(session_id, "assistant", result.answer)
        await self._short_term.add(
            MemoryEntry(
                content=result.answer,
                session_id=session_id,
                metadata={"role": "assistant", "mode": result.mode.value},
            )
        )

        # Store in long-term memory if available
        if self._long_term is not None:
            await self._long_term.add(
                MemoryEntry(
                    content=f"User: {query}\nAssistant: {result.answer}",
                    session_id=session_id,
                    metadata={"mode": result.mode.value},
                )
            )

        logger.info(
            "chat_complete",
            session_id=session_id,
            mode=result.mode.value,
            latency_ms=result.latency_ms,
            context_messages=len(context),
        )

        return ChatResponse(
            answer=result.answer,
            session_id=session_id,
            mode=result.mode.value,
            privacy_level=result.routing.privacy_level.value,
            complexity=result.routing.complexity.value,
            latency_ms=result.latency_ms,
            budget_remaining=self._budget.get_remaining(session_id),
        )

    async def _retrieve_context(
        self, query: str, session_id: str
    ) -> list[MemoryEntry]:
        """Retrieve relevant context from both short-term and long-term memory.

        Short-term: recent messages from the current session.
        Long-term: semantically similar past conversations.
        """
        context: list[MemoryEntry] = []

        # Short-term: recent session messages (always available)
        recent = await self._short_term.search(query, top_k=_MAX_CONTEXT_MESSAGES)
        session_recent = [e for e in recent if e.session_id == session_id]
        context.extend(session_recent)

        # Long-term: semantic search across all sessions
        if self._long_term is not None:
            try:
                long_term_hits = await self._long_term.search(
                    query, top_k=_MAX_CONTEXT_MESSAGES
                )
                context.extend(long_term_hits)
            except Exception as exc:
                logger.warning("long_term_search_failed", error=str(exc))

        return context

    @staticmethod
    def _enrich_query(query: str, context: list[MemoryEntry]) -> str:
        """Prepend relevant memory context to the user query.

        The enriched query gives the LLM access to conversation history
        without requiring it to be in the message window.
        """
        if not context:
            return query

        context_lines = []
        for entry in context:
            role = entry.metadata.get("role", "unknown")
            context_lines.append(f"[{role}] {entry.content}")

        context_block = "\n".join(context_lines)
        return (
            f"Relevant conversation history:\n{context_block}\n\n"
            f"Current question: {query}"
        )
