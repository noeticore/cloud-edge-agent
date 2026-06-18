"""SQLite-backed local memory store for sensitive data.

Stores privacy-sensitive conversations (S2/S3) locally instead of
sending them to cloud vector databases. This ensures sensitive data
like phone numbers, IDs, etc. never leave the local device.
"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from app.core.logger.logger import get_logger
from app.domain.memory.memory import MemoryEntry, MemoryStore, MemoryType

logger = get_logger(__name__)

# Default database path
DEFAULT_DB_PATH = "data/local_memory.db"


class SQLiteMemoryStore(MemoryStore):
    """Local SQLite memory store for privacy-sensitive conversations.

    Unlike Qdrant (cloud), this store keeps all data on the local device.
    Used for S2/S3 privacy level conversations.
    """

    memory_type = MemoryType.LONG_TERM

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        """Initialize SQLite store.

        Args:
            db_path: path to SQLite database file.
        """
        self._db_path = db_path
        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    session_id TEXT,
                    privacy_level TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_id
                ON memories(session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_privacy_level
                ON memories(privacy_level)
            """)
            conn.commit()
        logger.info("sqlite_db_initialized", path=self._db_path)

    async def add(self, entry: MemoryEntry) -> str:
        """Store a memory entry locally.

        Args:
            entry: memory entry to store.

        Returns:
            The entry ID.
        """
        entry_id = entry.entry_id or uuid.uuid4().hex
        privacy_level = entry.metadata.get("privacy_level", "unknown")

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memories (id, content, metadata, session_id, privacy_level)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    entry.content,
                    json.dumps(entry.metadata, ensure_ascii=False),
                    entry.session_id,
                    privacy_level,
                ),
            )
            conn.commit()

        logger.info(
            "sqlite_add",
            entry_id=entry_id,
            content_len=len(entry.content),
            privacy_level=privacy_level,
        )
        return entry_id

    async def search(self, query: str, top_k: int = 5) -> list[MemoryEntry]:
        """Search memories by keyword matching.

        Note: SQLite doesn't support vector similarity search.
        Uses LIKE matching with Chinese-aware tokenization.

        Args:
            query: search query.
            top_k: maximum results to return.

        Returns:
            List of matching MemoryEntry objects.
        """
        # Extract keywords: split by spaces, punctuation, and also use n-grams for Chinese
        import re

        # Remove punctuation and split by spaces
        cleaned = re.sub(r'[，。！？、；：""‘’“”（）\[\]【】\s]+', ' ', query)
        keywords = [kw.strip() for kw in cleaned.split() if len(kw.strip()) > 1]

        # Also extract 2-3 character n-grams from Chinese text for better matching
        chinese_chars = re.findall(r'[一-鿿]+', query)
        for segment in chinese_chars:
            # Add 2-character n-grams
            for i in range(len(segment) - 1):
                ngram = segment[i:i+2]
                if ngram not in keywords:
                    keywords.append(ngram)
            # Add 3-character n-grams for longer segments
            if len(segment) > 2:
                for i in range(len(segment) - 2):
                    ngram = segment[i:i+3]
                    if ngram not in keywords:
                        keywords.append(ngram)

        if not keywords:
            return await self.get_recent(limit=top_k)

        # Limit keywords to avoid too many OR conditions
        keywords = keywords[:20]

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            # Build LIKE conditions for each keyword
            conditions = " OR ".join(["content LIKE ?"] * len(keywords))
            params = [f"%{kw}%" for kw in keywords]

            cursor = conn.execute(
                f"""
                SELECT id, content, metadata, session_id, privacy_level, created_at
                FROM memories
                WHERE {conditions}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params + [top_k],
            )
            rows = cursor.fetchall()

        logger.info(
            "sqlite_search",
            query=query[:50],
            keywords_count=len(keywords),
            results=len(rows),
        )

        return [
            MemoryEntry(
                content=row["content"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                session_id=row["session_id"] or "",
                entry_id=row["id"],
            )
            for row in rows
        ]

    async def get_recent(self, limit: int = 10) -> list[MemoryEntry]:
        """Get most recent entries."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT id, content, metadata, session_id, privacy_level, created_at
                FROM memories
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()

        return [
            MemoryEntry(
                content=row["content"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                session_id=row["session_id"] or "",
                entry_id=row["id"],
            )
            for row in rows
        ]

    async def clear(self, session_id: str | None = None) -> None:
        """Clear memories (optionally filter by session)."""
        with sqlite3.connect(self._db_path) as conn:
            if session_id:
                conn.execute(
                    "DELETE FROM memories WHERE session_id = ?",
                    (session_id,),
                )
            else:
                conn.execute("DELETE FROM memories")
            conn.commit()
        logger.info("sqlite_clear", session_id=session_id)
