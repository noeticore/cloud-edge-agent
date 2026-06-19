"""SQLite-backed sanitization mapping store for cross-session reuse.

Stores mappings from placeholders to original values so that:
- The same original value always gets the same placeholder across sessions.
- Mappings persist across application restarts.
"""

import json
import sqlite3
import time
from pathlib import Path

from app.core.logger.logger import get_logger
from app.domain.privacy.privacy import (
    SanitizationMappingRecord,
    SanitizationMappingStore,
)

logger = get_logger(__name__)

DEFAULT_DB_PATH = "data/local_memory.db"


class SQLiteSanitizationMappingStore(SanitizationMappingStore):
    """SQLite implementation of SanitizationMappingStore.

    Global mapping table — one placeholder per original value across all sessions.
    """

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        # In-memory cache for fast lookup
        self._cache: dict[str, SanitizationMappingRecord] = {}
        self._reverse_cache: dict[str, SanitizationMappingRecord] = {}
        self._load_cache()

    def _init_db(self) -> None:
        """Initialize sanitization_mappings table."""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sanitization_mappings (
                    placeholder TEXT PRIMARY KEY,
                    original_value TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    usage_count INTEGER DEFAULT 0,
                    session_ids TEXT DEFAULT '[]'
                )
            """)
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_mapping_original
                ON sanitization_mappings(original_value)
            """)
            conn.commit()
        logger.info("mapping_table_initialized", path=self._db_path)

    def _load_cache(self) -> None:
        """Load all mappings into memory."""
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT placeholder, original_value, entity_type, "
                "created_at, usage_count, session_ids FROM sanitization_mappings"
            )
            rows = cursor.fetchall()

        self._cache = {}
        self._reverse_cache = {}
        for row in rows:
            record = SanitizationMappingRecord(
                placeholder=row["placeholder"],
                original_value=row["original_value"],
                entity_type=row["entity_type"],
                created_at=row["created_at"],
                usage_count=row["usage_count"],
                session_ids=json.loads(row["session_ids"]) if row["session_ids"] else [],
            )
            self._cache[record.placeholder] = record
            self._reverse_cache[record.original_value] = record

        logger.info("mapping_cache_loaded", count=len(self._cache))

    async def get_by_original(self, original_value: str) -> SanitizationMappingRecord | None:
        """Look up an existing mapping by original value."""
        return self._reverse_cache.get(original_value)

    async def get_by_placeholder(self, placeholder: str) -> SanitizationMappingRecord | None:
        """Look up an existing mapping by placeholder."""
        return self._cache.get(placeholder)

    async def save(self, record: SanitizationMappingRecord) -> None:
        """Save or update a mapping record."""
        now = record.created_at or time.time()
        session_ids_json = json.dumps(record.session_ids, ensure_ascii=False)

        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sanitization_mappings
                (placeholder, original_value, entity_type, created_at,
                 usage_count, session_ids)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.placeholder,
                    record.original_value,
                    record.entity_type,
                    now,
                    record.usage_count,
                    session_ids_json,
                ),
            )
            conn.commit()

        # Update cache
        self._cache[record.placeholder] = record
        self._reverse_cache[record.original_value] = record

        logger.info(
            "mapping_saved",
            placeholder=record.placeholder,
            entity_type=record.entity_type,
            usage_count=record.usage_count,
        )

    async def increment_usage(self, placeholder: str, session_id: str) -> None:
        """Increment usage count and add session ID."""
        record = self._cache.get(placeholder)
        if record is None:
            return

        record.usage_count += 1
        if session_id not in record.session_ids:
            record.session_ids.append(session_id)

        await self.save(record)

    async def load_all(self) -> dict[str, SanitizationMappingRecord]:
        """Load all mappings as {placeholder: record}."""
        return dict(self._cache)
