"""Privacy engine — three-layer detection pipeline: Regex → NER → SLM.

This is the concrete implementation of the privacy detection + sanitization
interfaces defined in domain/privacy.
"""

import re
import uuid

from app.core.logger.logger import get_logger
from app.domain.llm.llm_client import LLMClient, LLMMessage
from app.domain.privacy.privacy import (
    PrivacyDetection,
    PrivacyDetector,
    PrivacyLevel,
    SanitizationMappingRecord,
    SanitizationMappingStore,
    Sanitizer,
    SanitizeResult,
    SensitiveEntity,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Layer 1 — Regex patterns for common PII
# ---------------------------------------------------------------------------
_PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "PHONE": re.compile(
        r"(?<!\d)1[3-9]\d{9}(?!\d)"
    ),
    "ID_CARD": re.compile(
        r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
    ),
    "EMAIL": re.compile(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    ),
    "BANK_CARD": re.compile(
        r"(?<!\d)(?:6[0-9]{15,18}|4[0-9]{15}|5[1-5][0-9]{14}|3[47][0-9]{13})(?!\d)"
    ),
    "IP_ADDRESS": re.compile(
        r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?!\d)"
    ),
}


def _regex_detect(text: str) -> list[SensitiveEntity]:
    """Layer 1: Fast regex-based PII detection (~0ms)."""
    entities: list[SensitiveEntity] = []
    for entity_type, pattern in _PII_PATTERNS.items():
        for match in pattern.finditer(text):
            entities.append(
                SensitiveEntity(
                    entity_type=entity_type,
                    value=match.group(),
                    start=match.start(),
                    end=match.end(),
                )
            )
    return entities


# ---------------------------------------------------------------------------
# Layer 2 — NER (Presidio or simple keyword-based fallback)
# ---------------------------------------------------------------------------

async def _ner_detect(text: str) -> list[SensitiveEntity]:
    """Layer 2: Named Entity Recognition via Presidio.

    Falls back to a no-op if Presidio is not installed.
    """
    try:
        from presidio_analyzer import AnalyzerEngine

        analyzer = AnalyzerEngine()
        results = analyzer.analyze(text=text, language="en")
        return [
            SensitiveEntity(
                entity_type=result.entity_type,
                value=text[result.start : result.end],
                start=result.start,
                end=result.end,
            )
            for result in results
        ]
    except ImportError:
        logger.warning("presidio_not_installed", msg="Skipping NER layer")
        return []
    except Exception as exc:
        logger.warning("ner_failed", error=str(exc))
        return []


# ---------------------------------------------------------------------------
# Layer 3 — SLM privacy judge (Qwen 1.5B)
# ---------------------------------------------------------------------------

_SLM_JUDGE_PROMPT = """You are a privacy classifier. Analyze the following text and determine its privacy level.

Rules:
- S1 (Safe): No personal/sensitive information. General knowledge questions, public info.
- S2 (Sensitive): Contains some personal info that could be redacted (names, phone numbers, etc.)
- S3 (Confidential): Highly sensitive data that should NEVER leave the local device (bank statements, medical records, private keys, passwords)

Respond with ONLY a JSON object: {{"level": "S1|S2|S3", "confidence": 0.0-1.0, "reason": "brief explanation"}}

Text to analyze:
{text}"""


async def _slm_judge(text: str, slm_client: LLMClient) -> PrivacyDetection:
    """Layer 3: Use a small local LLM to judge privacy level."""
    messages = [
        LLMMessage(role="user", content=_SLM_JUDGE_PROMPT.format(text=text))
    ]
    try:
        response = await slm_client.invoke(messages)
        import json

        parsed = json.loads(response.content.strip())
        level = PrivacyLevel(parsed.get("level", "NA"))
        confidence = float(parsed.get("confidence", 0.5))
        reason = parsed.get("reason", "")
        return PrivacyDetection(
            level=level, confidence=confidence, reason=reason
        )
    except Exception as exc:
        logger.warning("slm_judge_failed", error=str(exc))
        return PrivacyDetection(
            level=PrivacyLevel.NA,
            confidence=0.0,
            reason=f"SLM judge failed, unable to determine: {exc}",
        )


# ---------------------------------------------------------------------------
# Keyword-based privacy detection (lightweight Layer 2)
# ---------------------------------------------------------------------------

_PRIVACY_KEYWORDS: dict[str, list[str]] = {
    "ADDRESS": [
        "住址", "地址", "家庭住址", "住在哪里", "住在哪", "家住",
        "小区", "号楼", "单元", "门牌", "街道", "路", "巷",
    ],
    "NAME": [
        "姓名", "名字", "叫什么", "是谁", "我朋友叫", "我同事叫",
        "我老板", "我领导", "我家人",
    ],
    "FINANCIAL": [
        "银行卡", "存款", "余额", "工资", "收入", "转账",
        "支付宝", "微信钱包", "信用卡",
    ],
    "MEDICAL": [
        "病历", "诊断", "医院", "处方", "用药", "病情",
        "体检", "化验",
    ],
}


def _keyword_detect(text: str) -> list[SensitiveEntity]:
    """Layer 2: Keyword-based detection for privacy-related topics.

    Catches semantic privacy concerns that regex cannot detect,
    such as asking about someone's address or name.
    """
    entities: list[SensitiveEntity] = []
    text_lower = text.lower()

    for entity_type, keywords in _PRIVACY_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text_lower:
                # Find the position of the keyword
                start = text_lower.find(keyword)
                end = start + len(keyword)
                entities.append(
                    SensitiveEntity(
                        entity_type=entity_type,
                        value=text[start:end],
                        start=start,
                        end=end,
                    )
                )
                break  # One match per type is enough

    return entities


# ---------------------------------------------------------------------------
# Composite Privacy Detector
# ---------------------------------------------------------------------------


class ThreeLayerPrivacyDetector(PrivacyDetector):
    """Privacy detection: Regex → Keywords → SLM.

    Detection pipeline:
    - Layer 1 (Regex): structured PII (phone, email, ID card, etc.)
    - Layer 2 (Keywords): semantic privacy topics (address, name, etc.)
    - Layer 3 (SLM): deep semantic analysis via local small model

    Short-circuit logic:
    - Layer 1 hit → S2 (high confidence, structured PII found)
    - Layer 2 hit → S2 (medium confidence, privacy topic detected)
    - Layer 3 available → use SLM result (S1/S2/S3)
    - Layer 3 unavailable + no hits → S1 (assume safe, rely on regex/keywords)

    SLM client MUST be local — never cloud.
    """

    def __init__(self, slm_client: LLMClient | None) -> None:
        self._slm_client = slm_client

    async def detect(self, text: str) -> PrivacyDetection:
        """Run the privacy detection pipeline."""
        # Layer 1: Regex — structured PII
        regex_entities = _regex_detect(text)
        if regex_entities:
            logger.info(
                "privacy_regex_hit",
                count=len(regex_entities),
                types=[e.entity_type for e in regex_entities],
            )
            return PrivacyDetection(
                level=PrivacyLevel.S2,
                confidence=0.95,
                entities=regex_entities,
                reason=f"Regex detected {len(regex_entities)} PII entities",
            )

        # Layer 2: Keywords — semantic privacy topics
        keyword_entities = _keyword_detect(text)
        if keyword_entities:
            logger.info(
                "privacy_keyword_hit",
                count=len(keyword_entities),
                types=[e.entity_type for e in keyword_entities],
            )
            return PrivacyDetection(
                level=PrivacyLevel.S2,
                confidence=0.7,
                entities=keyword_entities,
                reason=f"Keyword detected {len(keyword_entities)} privacy topics",
            )

        # Layer 3: SLM judge — deep semantic analysis
        if self._slm_client is not None:
            slm_result = await _slm_judge(text, self._slm_client)
            slm_result.entities = []
            logger.info(
                "privacy_slm_judge",
                level=slm_result.level.value,
                confidence=slm_result.confidence,
            )
            # If SLM says S2 or S3, trust it
            if slm_result.level in (PrivacyLevel.S2, PrivacyLevel.S3):
                return slm_result
            # If SLM says S1, return S1
            return PrivacyDetection(
                level=PrivacyLevel.S1,
                confidence=slm_result.confidence,
                reason=f"SLM judged as low risk: {slm_result.reason}",
            )

        # No SLM available — rely on regex/keywords only
        # If nothing triggered, assume S1 (safe)
        logger.info("privacy_all_clear", reason="no PII detected, SLM unavailable")
        return PrivacyDetection(
            level=PrivacyLevel.S1,
            confidence=0.5,
            reason="No PII detected (regex/keywords), SLM unavailable",
        )


# ---------------------------------------------------------------------------
# Sanitizer implementation
# ---------------------------------------------------------------------------


class RegexSanitizer(Sanitizer):
    """Replace sensitive entities with typed placeholders.

    Example: "我的手机是13812345678" → "我的手机是[REDACTED:PHONE:a3f2]"
    The mapping allows exact restoration.

    Supports cross-session mapping reuse via SanitizationMappingStore:
    - The same original value always gets the same placeholder.
    - Mappings persist across sessions and restarts.
    """

    def __init__(
        self,
        mapping_store: SanitizationMappingStore | None = None,
    ) -> None:
        """Initialize sanitizer.

        Args:
            mapping_store: optional persistent mapping store for cross-session reuse.
        """
        self._mapping_store = mapping_store

    async def sanitize(
        self,
        text: str,
        entities: list[SensitiveEntity],
        session_id: str = "",
    ) -> SanitizeResult:
        """Replace each entity with a placeholder.

        If mapping_store is available, reuses existing placeholders for
        known values to maintain cross-session consistency.
        """
        if not entities:
            return SanitizeResult(sanitized_text=text, mapping={}, entities_replaced=0)

        # Sort by start position descending to replace from end to start
        sorted_entities = sorted(entities, key=lambda e: e.start, reverse=True)
        sanitized = text
        mapping: dict[str, str] = {}

        for entity in sorted_entities:
            # Try to reuse existing placeholder for this value
            placeholder = await self._get_or_create_placeholder(
                entity.entity_type, entity.value, session_id
            )
            sanitized = sanitized[: entity.start] + placeholder + sanitized[entity.end :]
            mapping[placeholder] = entity.value

        return SanitizeResult(
            sanitized_text=sanitized,
            mapping=mapping,
            entities_replaced=len(entities),
        )

    async def _get_or_create_placeholder(
        self,
        entity_type: str,
        original_value: str,
        session_id: str = "",
    ) -> str:
        """Get existing placeholder or create a new one.

        If mapping_store is available, checks for existing mapping first.
        Otherwise, generates a new random placeholder.
        """
        # Check for existing mapping
        if self._mapping_store is not None:
            existing = await self._mapping_store.get_by_original(original_value)
            if existing is not None:
                # Update usage count
                await self._mapping_store.increment_usage(
                    existing.placeholder, session_id
                )
                logger.info(
                    "mapping_reused",
                    placeholder=existing.placeholder,
                    entity_type=entity_type,
                )
                return existing.placeholder

        # Create new placeholder
        tag = uuid.uuid4().hex[:6]
        placeholder = f"[REDACTED:{entity_type}:{tag}]"

        # Save to store if available
        if self._mapping_store is not None:
            import time

            record = SanitizationMappingRecord(
                placeholder=placeholder,
                original_value=original_value,
                entity_type=entity_type,
                created_at=time.time(),
                usage_count=1,
                session_ids=[session_id] if session_id else [],
            )
            await self._mapping_store.save(record)

        return placeholder

    async def restore(self, sanitized_text: str, mapping: dict[str, str]) -> str:
        """Restore original values from the mapping."""
        result = sanitized_text
        for placeholder, original in mapping.items():
            result = result.replace(placeholder, original)
        return result

    async def restore_from_store(self, sanitized_text: str) -> str:
        """Restore original values using the persistent mapping store.

        Useful when the local mapping dict is not available but the
        store has all mappings persisted.
        """
        if self._mapping_store is None:
            return sanitized_text

        # Find all placeholders in the text
        import re

        pattern = r'\[REDACTED:[A-Z_]+:[a-f0-9]{6}\]'
        placeholders = re.findall(pattern, sanitized_text)

        result = sanitized_text
        for placeholder in placeholders:
            record = await self._mapping_store.get_by_placeholder(placeholder)
            if record is not None:
                result = result.replace(placeholder, record.original_value)

        return result
