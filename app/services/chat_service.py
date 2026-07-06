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
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from app.core.logger.logger import get_logger
from app.core.trace.collector import save_trace, trace_context
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
        trace_enabled: bool = False,
        trace_output_dir: str = "outputs/traces",
    ) -> None:
        self._orchestrator = orchestrator
        self._sessions = session_store
        self._conversations = conversation_store
        self._rag = rag_pipeline
        self._trace_enabled = trace_enabled
        self._trace_output_dir = trace_output_dir

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

        # Wrap processing in trace context if enabled
        async with trace_context(session_id, query) if self._trace_enabled else self._noop_trace() as collector:
            result, context_messages = await self._process_query(
                query, session_id, collector
            )

            # Enrich trace with orchestrator result
            if collector is not None:
                collector.set_metadata("mode", result.mode.value)
                collector.set_metadata("privacy_level", result.routing.privacy_level.value)
                collector.set_metadata("complexity", result.routing.complexity.value)
                collector.set_metadata("answer", result.answer)
                if result.agent_result and result.agent_result.steps:
                    collector.set_metadata("agent_steps", len(result.agent_result.steps))
                    collector.set_metadata("total_tokens", result.agent_result.total_tokens)
                save_trace(collector, self._trace_output_dir)

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

    async def _process_query(
        self, query: str, session_id: str, collector: Any = None
    ) -> tuple[OrchestratorResult, list[dict[str, str]]]:
        """Core processing logic shared by chat and chat_stream.

        Returns:
            (orchestrator_result, context_messages)
        """
        # Retrieve conversation context
        context_messages = await self._conversations.get_context_messages(
            session_id=session_id,
            limit=_MAX_CONTEXT_MESSAGES,
            content_type="original",
        )

        # RAG retrieval
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

        # Local conversation search
        local_context = ""
        try:
            local_results = await self._conversations.search(
                query=query, session_id=None, top_k=3
            )
            recent_entries = await self._conversations.get_recent(
                limit=10, content_type="original"
            )

            seen: set[str] = set()
            merged: list = []
            for entry in local_results + recent_entries:
                if entry.entry_id not in seen:
                    seen.add(entry.entry_id)
                    merged.append(entry)

            if merged:
                local_context = "\n".join(
                    f"- [{e.role}] {e.original_content[:200]}"
                    for e in merged[:5]
                )
                logger.info(
                    "local_conversation_context",
                    query=query,
                    keyword_hits=len(local_results),
                    recent_entries=len(recent_entries),
                    merged=len(merged),
                )
        except Exception as exc:
            logger.warning("local_conversation_search_failed", error=str(exc))

        # Build enriched query
        enriched_query = self._enrich_query(
            query, context_messages, rag_context, local_context
        )

        # Run orchestrator
        result: OrchestratorResult = await self._orchestrator.process(
            query=enriched_query,
            session_id=session_id,
            context_messages=context_messages,
            raw_query=query,
        )

        return result, context_messages

    @staticmethod
    def _noop_trace():
        """No-op context manager when tracing is disabled."""
        @asynccontextmanager
        async def _noop():
            yield None
        return _noop()

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

        # Wrap in trace context if enabled
        async with trace_context(session_id, query) if self._trace_enabled else self._noop_trace() as collector:
            result, context_messages = await self._process_query(
                query, session_id, collector
            )

            # Enrich trace
            if collector is not None:
                collector.set_metadata("mode", result.mode.value)
                collector.set_metadata("privacy_level", result.routing.privacy_level.value)
                collector.set_metadata("complexity", result.routing.complexity.value)
                if result.agent_result and result.agent_result.steps:
                    collector.set_metadata("agent_steps", len(result.agent_result.steps))
                    collector.set_metadata("total_tokens", result.agent_result.total_tokens)
                save_trace(collector, self._trace_output_dir)

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
        yield json.dumps({
            "type": "done",
            "answer": answer,
            "latency_ms": result.latency_ms,
        }, ensure_ascii=False)

        logger.info(
            "chat_stream_complete",
            session_id=session_id,
            mode=processing_mode,
            latency_ms=result.latency_ms,
        )

    async def get_sessions(self) -> list[dict]:
        """List all conversation sessions with summary info."""
        return await self._conversations.get_sessions()

    async def get_session_messages(self, session_id: str) -> list[dict]:
        """Get all messages for a session as API-friendly dicts."""
        entries = await self._conversations.get_history(
            session_id, limit=200, content_type="original"
        )
        return [
            {
                "entry_id": e.entry_id,
                "session_id": e.session_id,
                "role": e.role,
                "content": e.original_content,
                "privacy_level": e.privacy_level,
                "processing_mode": e.processing_mode,
                "has_sensitive_data": e.has_sensitive_data,
                "timestamp": e.timestamp,
            }
            for e in entries
        ]

    @staticmethod
    def _enrich_query(
        query: str,
        context_messages: list[dict[str, str]],
        rag_context: str,
        local_context: str = "",
    ) -> str:
        """Build enriched query with conversation history and RAG context.

        The enriched query gives the LLM access to conversation history
        without requiring it to be in the message window.

        Context sources (in priority order):
        1. Current session conversation history
        2. RAG results from vector store (external knowledge)
        3. Local conversation search results (cross-session history)
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

        if local_context:
            parts.append(f"Relevant past conversations:\n{local_context}")

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
