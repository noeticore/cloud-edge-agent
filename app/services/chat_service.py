"""Chat service — business orchestration for the chat endpoint.

Flow: Request → Session → Memory Retrieval → Orchestrator → Memory Store → Response

Privacy-aware storage routing:
  - S1 (Safe) → Qdrant cloud (long-term memory)
  - S2/S3 (Sensitive/Confidential) → SQLite local (never leaves device)
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.core.logger.logger import get_logger
from app.domain.memory.memory import MemoryEntry, MemoryStore
from app.infrastructure.database.session_repository import InMemorySessionStore
from app.infrastructure.rag.pipeline import RAGPipeline
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


class ChatService:
    """Orchestrates a chat request through the full pipeline.

    Privacy-aware storage:
    - S1 conversations → cloud memory (Qdrant)
    - S2/S3 conversations → local memory (SQLite)
    """

    def __init__(
        self,
        orchestrator: CollaborativeOrchestrator,
        session_store: InMemorySessionStore,
        short_term_memory: MemoryStore,
        cloud_memory: MemoryStore | None = None,
        local_memory: MemoryStore | None = None,
        rag_pipeline: RAGPipeline | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._sessions = session_store
        self._short_term = short_term_memory
        self._cloud_memory = cloud_memory  # Qdrant (for S1)
        self._local_memory = local_memory  # SQLite (for S2/S3)
        self._rag = rag_pipeline

    async def chat(
        self, query: str, session_id: str | None = None
    ) -> ChatResponse:
        """Process a chat message end-to-end.

        1. Ensure session exists
        2. Retrieve relevant memory context
        3. Enrich query with context
        4. Run orchestrator (privacy → complexity → route → execute)
        5. Store user message and response in memory (privacy-aware routing)
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

        # Store user message in short-term memory
        self._sessions.add_message(session_id, "user", query)
        await self._short_term.add(
            MemoryEntry(content=query, session_id=session_id, metadata={"role": "user"})
        )

        # Store assistant response in short-term memory
        self._sessions.add_message(session_id, "assistant", result.answer)
        await self._short_term.add(
            MemoryEntry(
                content=result.answer,
                session_id=session_id,
                metadata={"role": "assistant", "mode": result.mode.value},
            )
        )

        # Privacy-aware long-term storage
        privacy_level = result.routing.privacy_level.value
        conversation_entry = MemoryEntry(
            content=f"User: {query}\nAssistant: {result.answer}",
            session_id=session_id,
            metadata={"mode": result.mode.value, "privacy_level": privacy_level},
        )

        if privacy_level == "S1":
            # Safe → store in cloud (Qdrant)
            if self._cloud_memory is not None:
                await self._cloud_memory.add(conversation_entry)
                logger.info("stored_in_cloud", privacy_level=privacy_level)
        else:
            # S2/S3 → store locally (SQLite)
            if self._local_memory is not None:
                await self._local_memory.add(conversation_entry)
                logger.info("stored_locally", privacy_level=privacy_level)

        logger.info(
            "chat_complete",
            session_id=session_id,
            mode=result.mode.value,
            privacy_level=privacy_level,
            latency_ms=result.latency_ms,
            context_messages=len(context),
        )

        return ChatResponse(
            answer=result.answer,
            session_id=session_id,
            mode=result.mode.value,
            privacy_level=privacy_level,
            complexity=result.routing.complexity.value,
            latency_ms=result.latency_ms,
        )

    async def chat_stream(
        self, query: str, session_id: str | None = None
    ) -> AsyncIterator[str]:
        """Process a chat message and stream the response.

        This runs the full orchestrator pipeline (privacy detection, routing,
        agent execution) and then streams the final answer token by token
        for a better user experience.

        Yields:
            JSON strings with either token data or metadata.
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

        # Run orchestrator (full pipeline)
        result: OrchestratorResult = await self._orchestrator.process(
            query=enriched_query, session_id=session_id
        )

        # Store in memory
        self._sessions.add_message(session_id, "user", query)
        await self._short_term.add(
            MemoryEntry(content=query, session_id=session_id, metadata={"role": "user"})
        )
        self._sessions.add_message(session_id, "assistant", result.answer)
        await self._short_term.add(
            MemoryEntry(
                content=result.answer,
                session_id=session_id,
                metadata={"role": "assistant", "mode": result.mode.value},
            )
        )

        # Privacy-aware long-term storage
        privacy_level = result.routing.privacy_level.value
        conversation_entry = MemoryEntry(
            content=f"User: {query}\nAssistant: {result.answer}",
            session_id=session_id,
            metadata={"mode": result.mode.value, "privacy_level": privacy_level},
        )
        if privacy_level == "S1":
            if self._cloud_memory is not None:
                await self._cloud_memory.add(conversation_entry)
        else:
            if self._local_memory is not None:
                await self._local_memory.add(conversation_entry)

        # Stream metadata first
        metadata = {
            "type": "metadata",
            "session_id": session_id,
            "mode": result.mode.value,
            "privacy_level": result.routing.privacy_level.value,
            "complexity": result.routing.complexity.value,
            "latency_ms": result.latency_ms,
        }
        yield json.dumps(metadata, ensure_ascii=False)

        # Stream the answer token by token (simulated for demo)
        answer = result.answer
        chunk_size = 3  # characters per chunk
        for i in range(0, len(answer), chunk_size):
            chunk = answer[i : i + chunk_size]
            yield json.dumps({"type": "token", "delta": chunk}, ensure_ascii=False)
            await asyncio.sleep(0.02)  # Small delay for visual effect

        # Signal completion
        yield json.dumps({"type": "done"})

        logger.info(
            "chat_stream_complete",
            session_id=session_id,
            mode=result.mode.value,
            latency_ms=result.latency_ms,
        )

    async def _retrieve_context(
        self, query: str, session_id: str
    ) -> list[MemoryEntry]:
        """Retrieve relevant context from all memory sources.

        Sources:
        1. Short-term: recent messages from the current session
        2. Cloud memory (Qdrant): S1 past conversations
        3. Local memory (SQLite): S2/S3 past conversations
        4. RAG: relevant document chunks (if pipeline available)
        """
        context: list[MemoryEntry] = []

        # Short-term: recent session messages (always available)
        recent = await self._short_term.search(query, top_k=_MAX_CONTEXT_MESSAGES)
        session_recent = [e for e in recent if e.session_id == session_id]
        context.extend(session_recent)

        # Cloud memory: S1 conversations (Qdrant vector search)
        if self._cloud_memory is not None:
            try:
                cloud_hits = await self._cloud_memory.search(
                    query, top_k=_MAX_CONTEXT_MESSAGES
                )
                logger.info(
                    "cloud_memory_search",
                    query=query[:50],
                    hits=len(cloud_hits),
                    contents=[h.content[:50] for h in cloud_hits],
                )
                context.extend(cloud_hits)
            except Exception as exc:
                logger.warning("cloud_memory_search_failed", error=str(exc))

        # Local memory: S2/S3 conversations (SQLite keyword search)
        if self._local_memory is not None:
            try:
                local_hits = await self._local_memory.search(
                    query, top_k=_MAX_CONTEXT_MESSAGES
                )
                logger.info(
                    "local_memory_search",
                    query=query[:50],
                    hits=len(local_hits),
                    contents=[h.content[:50] for h in local_hits],
                )
                context.extend(local_hits)
            except Exception as exc:
                logger.warning("local_memory_search_failed", error=str(exc))

        # RAG: document retrieval
        if self._rag is not None:
            try:
                rag_results = await self._rag.retrieve(query, top_k=_MAX_CONTEXT_MESSAGES)
                for result in rag_results:
                    context.append(
                        MemoryEntry(
                            content=result.document.content,
                            metadata={
                                **result.document.metadata,
                                "source": "rag",
                                "score": result.score,
                            },
                            session_id=session_id,
                        )
                    )
            except Exception as exc:
                logger.warning("rag_retrieval_failed", error=str(exc))

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
            # Truncate long entries to avoid overwhelming the LLM
            content = entry.content[:500] if len(entry.content) > 500 else entry.content
            context_lines.append(f"[{role}] {content}")

        context_block = "\n".join(context_lines)

        # Limit total context length to avoid token limits
        max_context_len = 2000
        if len(context_block) > max_context_len:
            context_block = context_block[:max_context_len] + "\n... (context truncated)"

        logger.info(
            "enrich_query",
            context_entries=len(context),
            context_len=len(context_block),
        )

        return (
            f"Relevant conversation history:\n{context_block}\n\n"
            f"Current question: {query}"
        )
