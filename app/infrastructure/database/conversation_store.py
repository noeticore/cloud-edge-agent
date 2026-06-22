"""SQLite-backed conversation store with dual-content support.

Stores conversation entries with three content variants:
  - original_content: full text with real privacy data (local only)
  - sanitized_content: text with placeholders (sent to cloud)
  - restored_content: cloud response with placeholders restored
"""

import json
import re
import sqlite3
import uuid
from pathlib import Path

from app.core.logger.logger import get_logger
from app.domain.memory.memory import ConversationEntry, ConversationStore

logger = get_logger(__name__)

DEFAULT_DB_PATH = "data/local_memory.db"


class SQLiteConversationStore(ConversationStore):
    """SQLite implementation of ConversationStore.

    All conversation data stays on the local device.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize conversations table."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    original_content TEXT NOT NULL,
                    sanitized_content TEXT DEFAULT '',
                    restored_content TEXT DEFAULT '',
                    privacy_level TEXT DEFAULT 'NA',
                    processing_mode TEXT DEFAULT 'direct_local',
                    has_sensitive_data INTEGER DEFAULT 0,
                    timestamp REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_conv_session
                ON conversations(session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_conv_timestamp
                ON conversations(timestamp)
            """)
            conn.commit()
        logger.info("conversation_table_initialized", path=self._db_path)

    async def add(self, entry: ConversationEntry) -> str:
        """Store a conversation entry."""
        entry_id = entry.entry_id or uuid.uuid4().hex
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO conversations
                (id, session_id, role, original_content, sanitized_content,
                 restored_content, privacy_level, processing_mode,
                 has_sensitive_data, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    entry.session_id,
                    entry.role,
                    entry.original_content,
                    entry.sanitized_content,
                    entry.restored_content,
                    entry.privacy_level,
                    entry.processing_mode,
                    1 if entry.has_sensitive_data else 0,
                    entry.timestamp,
                ),
            )
            conn.commit()
        logger.info(
            "conversation_added",
            entry_id=entry_id,
            session_id=entry.session_id,
            role=entry.role,
            privacy_level=entry.privacy_level,
            has_sensitive=entry.has_sensitive_data,
        )
        return entry_id

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
        content_col = "sanitized_content" if content_type == "sanitized" else "original_content"

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f"""
                SELECT id, session_id, role, original_content, sanitized_content,
                       restored_content, privacy_level, processing_mode,
                       has_sensitive_data, timestamp
                FROM conversations
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = cursor.fetchall()

        entries = []
        for row in reversed(rows):  # Reverse to get chronological order
            entries.append(
                ConversationEntry(
                    entry_id=row["id"],
                    session_id=row["session_id"],
                    role=row["role"],
                    original_content=row["original_content"],
                    sanitized_content=row["sanitized_content"] or "",
                    restored_content=row["restored_content"] or "",
                    privacy_level=row["privacy_level"] or "NA",
                    processing_mode=row["processing_mode"] or "direct_local",
                    has_sensitive_data=bool(row["has_sensitive_data"]),
                    timestamp=row["timestamp"],
                )
            )

        return entries

    async def get_recent(
        self, limit: int = 10, content_type: str = "original"
    ) -> list[ConversationEntry]:
        """Get most recent conversation entries across ALL sessions.

        Useful for cross-session context retrieval (e.g. user shared
        contact info in a previous session and asks about it now).

        Args:
            limit: max entries to return.
            content_type: "original" for local, "sanitized" for cloud.
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT id, session_id, role, original_content, sanitized_content,
                       restored_content, privacy_level, processing_mode,
                       has_sensitive_data, timestamp
                FROM conversations
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()

        entries = []
        for row in reversed(rows):
            entries.append(
                ConversationEntry(
                    entry_id=row["id"],
                    session_id=row["session_id"],
                    role=row["role"],
                    original_content=row["original_content"],
                    sanitized_content=row["sanitized_content"] or "",
                    restored_content=row["restored_content"] or "",
                    privacy_level=row["privacy_level"] or "NA",
                    processing_mode=row["processing_mode"] or "direct_local",
                    has_sensitive_data=bool(row["has_sensitive_data"]),
                    timestamp=row["timestamp"],
                )
            )
        return entries

    async def get_context_messages(
        self,
        session_id: str,
        limit: int = 10,
        content_type: str = "original",
    ) -> list[dict[str, str]]:
        """Get conversation history as message dicts for LLM context.

        For sanitized mode, uses sanitized_content for sensitive messages
        and original_content for non-sensitive messages.

        Args:
            session_id: session to retrieve.
            limit: max messages to return.
            content_type: "original" or "sanitized".
        """
        entries = await self.get_history(session_id, limit, content_type="original")

        messages = []
        for entry in entries:
            if content_type == "sanitized" and entry.has_sensitive_data:
                # Use sanitized content for sensitive messages
                content = entry.sanitized_content or entry.original_content
            elif content_type == "sanitized" and entry.restored_content:
                # Use restored content for assistant responses in sanitize mode
                content = entry.restored_content
            else:
                content = entry.original_content

            messages.append({"role": entry.role, "content": content})

        return messages

    async def search(
        self, query: str, session_id: str | None = None, top_k: int = 5
    ) -> list[ConversationEntry]:
        """Search conversations by keyword matching."""
        cleaned = re.sub(r'[，。！？、；：“”‘’（）\[\]【】\s]+', ' ', query)
        keywords = [kw.strip() for kw in cleaned.split() if len(kw.strip()) > 1]

        # Add 2-3 char n-grams for Chinese
        chinese_chars = re.findall(r'[一-鿿]+', query)
        for segment in chinese_chars:
            for i in range(len(segment) - 1):
                ngram = segment[i:i+2]
                if ngram not in keywords:
                    keywords.append(ngram)
            if len(segment) > 2:
                for i in range(len(segment) - 2):
                    ngram = segment[i:i+3]
                    if ngram not in keywords:
                        keywords.append(ngram)

        if not keywords:
            return []

        keywords = keywords[:20]

        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            conditions = " OR ".join(["original_content LIKE ?"] * len(keywords))
            params: list = [f"%{kw}%" for kw in keywords]

            if session_id:
                query_sql = f"""
                    SELECT id, session_id, role, original_content, sanitized_content,
                           restored_content, privacy_level, processing_mode,
                           has_sensitive_data, timestamp
                    FROM conversations
                    WHERE ({conditions}) AND session_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """
                params.append(session_id)
                params.append(top_k)
            else:
                query_sql = f"""
                    SELECT id, session_id, role, original_content, sanitized_content,
                           restored_content, privacy_level, processing_mode,
                           has_sensitive_data, timestamp
                    FROM conversations
                    WHERE {conditions}
                    ORDER BY timestamp DESC
                    LIMIT ?
                """
                params.append(top_k)

            cursor = conn.execute(query_sql, params)
            rows = cursor.fetchall()

        return [
            ConversationEntry(
                entry_id=row["id"],
                session_id=row["session_id"],
                role=row["role"],
                original_content=row["original_content"],
                sanitized_content=row["sanitized_content"] or "",
                restored_content=row["restored_content"] or "",
                privacy_level=row["privacy_level"] or "NA",
                processing_mode=row["processing_mode"] or "direct_local",
                has_sensitive_data=bool(row["has_sensitive_data"]),
                timestamp=row["timestamp"],
            )
            for row in rows
        ]

    async def get_sessions(self) -> list[dict]:
        """List all unique sessions with summary info.

        Returns:
            list of dicts with session_id, last_active, message_count, preview.
        """
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """SELECT session_id,
                          MAX(timestamp) AS last_active,
                          COUNT(*) AS message_count
                   FROM conversations
                   GROUP BY session_id
                   ORDER BY last_active DESC"""
            )
            sessions = []
            for row in cursor.fetchall():
                preview_cursor = conn.execute(
                    """SELECT original_content FROM conversations
                      WHERE session_id = ? AND role = 'user'
                      ORDER BY timestamp ASC LIMIT 1""",
                    (row["session_id"],),
                )
                preview_row = preview_cursor.fetchone()
                preview = (
                    preview_row["original_content"][:80] if preview_row else ""
                )
                sessions.append(
                    {
                        "session_id": row["session_id"],
                        "last_active": row["last_active"],
                        "message_count": row["message_count"],
                        "preview": preview,
                    }
                )
            return sessions

    async def clear(self, session_id: str | None = None) -> None:
        """Clear conversations."""
        with sqlite3.connect(self._db_path) as conn:
            if session_id:
                conn.execute(
                    "DELETE FROM conversations WHERE session_id = ?",
                    (session_id,),
                )
            else:
                conn.execute("DELETE FROM conversations")
            conn.commit()
        logger.info("conversations_cleared", session_id=session_id)
