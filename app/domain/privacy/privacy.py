"""Privacy engine abstractions — detection and sanitization."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Privacy levels (S1 = safe, S2 = sensitive, S3 = confidential)
# ---------------------------------------------------------------------------

class PrivacyLevel(str, Enum):
    S1 = "S1"      # safe — can go to cloud directly
    S2 = "S2"      # sensitive — sanitize before cloud
    S3 = "S3"      # confidential — must stay local
    NA = "N/A"     # not applicable — privacy detection skipped (low complexity)


@dataclass
class PrivacyDetection:
    """Result of privacy analysis on a text."""

    level: PrivacyLevel
    confidence: float
    entities: list["SensitiveEntity"] = field(default_factory=list)
    reason: str = ""


@dataclass
class SensitiveEntity:
    """A detected sensitive entity in text."""

    entity_type: str  # PHONE, ID_CARD, EMAIL, NAME, ADDRESS, etc.
    value: str
    start: int
    end: int


@dataclass
class SanitizeResult:
    """Output of the sanitization process."""

    sanitized_text: str
    mapping: dict[str, str]  # placeholder → original value
    entities_replaced: int


# ---------------------------------------------------------------------------
# Abstract interfaces
# ---------------------------------------------------------------------------

class PrivacyDetector(ABC):
    """Detect privacy level of input text (Layer 1-3 pipeline)."""

    @abstractmethod
    async def detect(self, text: str) -> PrivacyDetection:
        """Analyze text and return privacy level + detected entities."""
        ...


class Sanitizer(ABC):
    """Sanitize (redact) sensitive information from text."""

    @abstractmethod
    async def sanitize(
        self,
        text: str,
        entities: list[SensitiveEntity],
        session_id: str = "",
    ) -> SanitizeResult:
        """Replace sensitive entities with placeholders."""
        ...

    @abstractmethod
    async def restore(self, sanitized_text: str, mapping: dict[str, str]) -> str:
        """Restore original values from placeholder mapping."""
        ...


# ---------------------------------------------------------------------------
# Cross-session sanitization mapping
# ---------------------------------------------------------------------------


@dataclass
class SanitizationMappingRecord:
    """A persistent sanitization mapping for cross-session reuse.

    Attributes:
        placeholder: the placeholder string, e.g. [REDACTED:PHONE:abc123].
        original_value: the real value that was replaced.
        entity_type: PHONE, EMAIL, ID_CARD, ADDRESS, etc.
        created_at: unix timestamp when first created.
        usage_count: how many times this mapping has been used.
        session_ids: list of session IDs that used this mapping.
    """

    placeholder: str
    original_value: str
    entity_type: str
    created_at: float = 0.0
    usage_count: int = 0
    session_ids: list[str] = field(default_factory=list)


class SanitizationMappingStore(ABC):
    """Abstract storage for cross-session sanitization mappings."""

    @abstractmethod
    async def get_by_original(self, original_value: str) -> SanitizationMappingRecord | None:
        """Look up an existing mapping by original value."""
        ...

    @abstractmethod
    async def get_by_placeholder(self, placeholder: str) -> SanitizationMappingRecord | None:
        """Look up an existing mapping by placeholder."""
        ...

    @abstractmethod
    async def save(self, record: SanitizationMappingRecord) -> None:
        """Save or update a mapping record."""
        ...

    @abstractmethod
    async def load_all(self) -> dict[str, SanitizationMappingRecord]:
        """Load all mappings as {placeholder: record}."""
        ...
