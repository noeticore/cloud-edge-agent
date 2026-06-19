"""Chat service — business orchestration for the chat endpoint.

Flow: Request → Session → Context Retrieval → Orchestrator → Store → Response

Storage architecture:
  - All conversations stored locally in SQLite (ConversationStore)
  - Dual content: original (local) + sanitized/restored (for cloud context)
  - RAG pipeline uses Qdrant for document retrieval only (not conversation storage)

Context building:
  - Direct local/cloud modes: use original_content
  - Sanitize-cloud mode: use sanitized_content for sensitive messages,
    original_content for non-sensitive messages
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from app.core.logger.logger import get_logger
from app.domain.memory.memory import ConversationEntry, ConversationStore
from app.infrastructure.database.session_repository import InMemorySessionStore
from app.infrastructure.rag.pipeline import RAGPipeline
from app.services.agent_orchestrator import (
    CollaborativeOrchestrator,
    OrchestratorResult,
)

logger = get_logger(__name__)

# Maximum number of recent messages to inject as context
_MAX_CONTEXT_MESSAGES = 10


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

    Uses ConversationStore for persistent conversation storage with
    dual-content support (original + sanitized).
    """

    def __init__(
        self,
        orchestrator: CollaborativeOrchestrator,
        session_store: InMemorySessionStore,
        conversation_store: ConversationStore,
        rag_pipeline: RAGPipeline | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._sessions = session_store
        self._conversations = conversation_store
        self._rag = rag_pipeline

    async def chat(
        self, query: str, session_id: str | None = None
    ) -> ChatResponse:
        """Process a chat message end-to-end.

        1. Ensure session exists
        2. Retrieve conversation context
        3. Enrich query with RAG context if available
        4. Run orchestrator (privacy → complexity → route → execute)
        5. Store conversation with dual content
        6. Return structured response
        """
        # Ensure session
        if session_id is None:
            import uuid
            session_id = uuid.uuid4().hex[:12]
        if self._sessions.get(session_id) is None:
            self._sessions.create(session_id)

        # Retrieve conversation context for the orchestrator
        # The orchestrator will decide which content type to use based on mode
        context_messages = await self._conversations.get_context_messages(
            session_id=session_id,
            limit=_MAX_CONTEXT_MESSAGES,
            content_type="original",  # Default to original; orchestrator handles mode
        )

        # Also get RAG context if available
        rag_context = ""
        if self._rag is not None:
            try:
                rag_results = await self._rag.retrieve(query, top_k=3)
                if rag_results:
                    rag_context = "\n".join(
                        f"- {r.document.content[:200]}" for r in rag_results
                    )
            except Exception as exc:
                logger.warning("rag_retrieval_failed", error=str(exc))

        # Build enriched query
        enriched_query = self._enrich_query(query, context_messages, rag_context)

        # Run orchestrator
        result: OrchestratorResult = await self._orchestrator.process(
            query=enriched_query,
            session_id=session_id,
            context_messages=context_messages,
        )

        # Determine privacy and storage details
        privacy_level = result.routing.privacy_level.value
        processing_mode = result.mode.value
        has_sensitive = privacy_level in ("S2", "S3")

        # Store user message
        user_entry = ConversationEntry(
            session_id=session_id,
            role="user",
            original_content=query,
            sanitized_content=result.sanitized_query if has_sensitive else "",
            privacy_level=privacy_level,
            processing_mode=processing_mode,
            has_sensitive_data=has_sensitive,
            timestamp=time.time(),
        )
        await self._conversations.add(user_entry)

        # Store assistant response
        assistant_entry = ConversationEntry(
            session_id=session_id,
            role="assistant",
            original_content=result.answer,
            sanitized_content="",  # Assistant response is already restored
            restored_content=result.answer if has_sensitive else "",
            privacy_level=privacy_level,
            processing_mode=processing_mode,
            has_sensitive_data=False,  # Assistant response doesn't contain user PII
            timestamp=time.time(),
        )
        await self._conversations.add(assistant_entry)

        # Update session store (for API compatibility)
        self._sessions.add_message(session_id, "user", query)
        self._sessions.add_message(session_id, "assistant", result.answer)

        logger.info(
            "chat_complete",
            session_id=session_id,
            mode=processing_mode,
            privacy_level=privacy_level,
            latency_ms=result.latency_ms,
            context_messages=len(context_messages),
        )

        return ChatResponse(
            answer=result.answer,
            session_id=session_id,
            mode=processing_mode,
            privacy_level=privacy_level,
            complexity=result.routing.complexity.value,
            latency_ms=result.latency_ms,
        )

    async def chat_stream(
        self, query: str, session_id: str | None = None
    ) -> AsyncIterator[str]:
        """Process a chat message and stream the response.

        Yields:
            JSON strings with either token data or metadata.
        """
        # Ensure session
        if session_id is None:
            import uuid
            session_id = uuid.uuid4().hex[:12]
        if self._sessions.get(session_id) is None:
            self._sessions.create(session_id)

        # Retrieve context
        context_messages = await self._conversations.get_context_messages(
            session_id=session_id,
            limit=_MAX_CONTEXT_MESSAGES,
            content_type="original",
        )
        enriched_query = self._enrich_query(query, context_messages, "")

        # Run orchestrator
        result: OrchestratorResult = await self._orchestrator.process(
            query=enriched_query,
            session_id=session_id,
            context_messages=context_messages,
        )

        # Store conversation
        privacy_level = result.routing.privacy_level.value
        processing_mode = result.mode.value
        has_sensitive = privacy_level in ("S2", "S3")

        user_entry = ConversationEntry(
            session_id=session_id,
            role="user",
            original_content=query,
            sanitized_content=result.sanitized_query if has_sensitive else "",
            privacy_level=privacy_level,
            processing_mode=processing_mode,
            has_sensitive_data=has_sensitive,
            timestamp=time.time(),
        )
        assistant_entry = ConversationEntry(
            session_id=session_id,
            role="assistant",
            original_content=result.answer,
            sanitized_content="",
            restored_content=result.answer if has_sensitive else "",
            privacy_level=privacy_level,
            processing_mode=processing_mode,
            has_sensitive_data=False,
            timestamp=time.time(),
        )
        await self._conversations.add(user_entry)
        await self._conversations.add(assistant_entry)

        # Update session store
        self._sessions.add_message(session_id, "user", query)
        self._sessions.add_message(session_id, "assistant", result.answer)

        # Stream metadata first
        metadata = {
            "type": "metadata",
            "session_id": session_id,
            "mode": processing_mode,
            "privacy_level": privacy_level,
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
            await asyncio.sleep(0.02)

        # Signal completion
        yield json.dumps({"type": "done"})

        logger.info(
            "chat_stream_complete",
            session_id=session_id,
            mode=processing_mode,
            latency_ms=result.latency_ms,
        )

    @staticmethod
    def _enrich_query(
        query: str,
        context_messages: list[dict[str, str]],
        rag_context: str,
    ) -> str:
        """Build enriched query with conversation history and RAG context.

        The enriched query gives the LLM access to conversation history
        without requiring it to be in the message window.
        """
        parts = []

        if context_messages:
            context_lines = []
            for msg in context_messages:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")[:500]
                context_lines.append(f"[{role}] {content}")
            context_block = "\n".join(context_lines)

            # Limit total context length
            max_context_len = 2000
            if len(context_block) > max_context_len:
                context_block = context_block[:max_context_len] + "\n... (truncated)"

            parts.append(f"Relevant conversation history:\n{context_block}")

        if rag_context:
            parts.append(f"Relevant documents:\n{rag_context}")

        if parts:
            parts.append(f"Current question: {query}")
            enriched = "\n\n".join(parts)
        else:
            enriched = query

        logger.info(
            "enrich_query",
            context_messages=len(context_messages),
            has_rag=bool(rag_context),
            enriched_len=len(enriched),
        )

        return enriched
