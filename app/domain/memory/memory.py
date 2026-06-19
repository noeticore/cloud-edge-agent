"""Memory abstractions — short-term (conversation) and long-term (vector) memory."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from time import time


class MemoryType(str, Enum):
    SHORT_TERM = "short_term"
    LONG_TERM = "long_term"


@dataclass
class MemoryEntry:
    """A single memory record."""

    content: str
    metadata: dict = field(default_factory=dict)
    session_id: str = ""
    entry_id: str = ""
    score: float = 0.0


class MemoryStore(ABC):
    """Abstract memory storage.

    Implementations: InMemoryStore (short-term), VectorMemoryStore (long-term).
    """

    memory_type: MemoryType

    @abstractmethod
    async def add(self, entry: MemoryEntry) -> str:
        """Store a memory entry. Returns entry ID."""
        ...

    @abstractmethod
    async def search(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        """Retrieve relevant memories by semantic similarity."""
        ...

    @abstractmethod
    async def get_recent(self, limit: int = 10) -> list[MemoryEntry]:
        """Get the most recent memories (for short-term context window)."""
        ...

    @abstractmethod
    async def clear(self, session_id: str | None = None) -> None:
        """Clear memories, optionally scoped to a session."""
        ...


# ---------------------------------------------------------------------------
# Conversation storage — dual-content for privacy-aware routing
# ---------------------------------------------------------------------------


@dataclass
class ConversationEntry:
    """A conversation message with dual content for privacy-aware storage.

    Attributes:
        session_id: session identifier.
        role: "user" or "assistant".
        timestamp: unix timestamp.
        original_content: full content with real privacy data (local only).
        sanitized_content: content with placeholders (sent to cloud).
        restored_content: cloud response with placeholders restored.
        privacy_level: S1/S2/S3/NA.
        processing_mode: direct_local/direct_cloud/sanitize_cloud.
        has_sensitive_data: whether this message contains sensitive data.
    """

    session_id: str
    role: str
    original_content: str
    sanitized_content: str = ""
    restored_content: str = ""
    privacy_level: str = "NA"
    processing_mode: str = "direct_local"
    has_sensitive_data: bool = False
    timestamp: float = field(default_factory=time)
    entry_id: str = ""


class ConversationStore(ABC):
    """Abstract conversation storage with dual-content support.

    Stores both original (privacy-safe, local only) and sanitized
    (cloud-safe) versions of each message.
    """

    @abstractmethod
    async def add(self, entry: ConversationEntry) -> str:
        """Store a conversation entry. Returns entry ID."""
        ...

    @abstractmethod
    async def get_history(
        self,
        session_id: str,
        limit: int = 10,
        content_type: str = "original",
    ) -> list[ConversationEntry]:
        """Get conversation history for a session.

        Args:
            session_id: session to retrieve.
            limit: max entries to return.
            content_type: "original" for local, "sanitized" for cloud context.
        """
        ...

    @abstractmethod
    async def search(
        self, query: str, session_id: str | None = None, top_k: int = 5
    ) -> list[ConversationEntry]:
        """Search conversations by keyword matching."""
        ...

    @abstractmethod
    async def clear(self, session_id: str | None = None) -> None:
        """Clear conversations, optionally scoped to a session."""
        ...
