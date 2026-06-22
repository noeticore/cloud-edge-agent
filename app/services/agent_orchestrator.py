"""Agent orchestrator — the core collaborative engine.

Implements 4 modes:
  Mode A: Direct Local    — user → edge agent → answer
  Mode B: Direct Cloud    — user → cloud agent → answer
  Mode C: Sanitize-Cloud  — user → sanitize → cloud → restore → answer
  Mode D: Sketch-Refine   — edge sketch → cloud refine → edge restore (reserved)

Routing matrix:
  Simple + S1/S2/S3 → Mode A (local)
  Complex + S1      → Mode B (cloud)
  Complex + S2/S3   → Mode C (sanitize-cloud)
"""

import json
import re
import time
from dataclasses import dataclass, field

from app.core.logger.logger import get_logger
from app.domain.agent.agent import BaseAgent
from app.domain.llm.llm_client import LLMClient, LLMMessage
from app.domain.privacy.policy import (
    CollaborateMode,
    ComplexityLevel,
    RoutingResult,
    route,
)
from app.domain.privacy.privacy import (
    PrivacyDetection,
    PrivacyDetector,
    PrivacyLevel,
    Sanitizer,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Task complexity analyzer (simple LLM-based)
# ---------------------------------------------------------------------------

_COMPLEXITY_PROMPT = """Classify the complexity of the following task.

Levels:
- L1: FAQ / simple lookup
- L2: single-step reasoning
- L3: multi-step reasoning
- L4: agent task (requires tool use)
- L5: long-chain complex task

Respond with ONLY a JSON object: {{"level": "L1|L2|L3|L4|L5"}}

Task:
{text}"""


async def analyze_complexity(text: str, edge_client: LLMClient) -> ComplexityLevel:
    """Use the edge SLM to classify task complexity."""
    messages = [LLMMessage(role="user", content=_COMPLEXITY_PROMPT.format(text=text))]
    try:
        response = await edge_client.invoke(messages)
        content = response.content.strip()
        # Try direct JSON parse first
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            # Extract JSON object from markdown code blocks or extra text
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
            else:
                raise
        return ComplexityLevel[parsed["level"]]
    except Exception as exc:
        logger.warning("complexity_analysis_failed", error=str(exc))
        return ComplexityLevel.L3  # safe default


# ---------------------------------------------------------------------------
# Orchestrator result
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorResult:
    """Final result from the orchestrator."""

    answer: str
    mode: CollaborateMode
    routing: RoutingResult
    privacy_detection: PrivacyDetection
    latency_ms: float
    tokens_used: int = 0
    sanitized_query: str = ""
    restore_mapping: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Collaborative Orchestrator
# ---------------------------------------------------------------------------


class CollaborativeOrchestrator:
    """The main orchestrator that coordinates edge + cloud agents.

    Flow:
    1. Analyze task complexity (edge SLM) — cheap, always available
    2. Run privacy detection (regex → keywords → SLM)
    3. Route based on privacy × complexity matrix
    4. Execute the chosen collaborate mode

    For low-complexity tasks, privacy detection is still run but
    the routing always selects local mode regardless of result.
    This ensures we correctly identify sensitive data for storage
    routing (original vs sanitized content).
    """

    def __init__(
        self,
        edge_client: LLMClient,
        cloud_client: LLMClient,
        edge_agent: BaseAgent,
        cloud_agent: BaseAgent,
        privacy_detector: PrivacyDetector,
        sanitizer: Sanitizer,
        edge_available: bool = True,
    ) -> None:
        self._edge = edge_client
        self._cloud = cloud_client
        self._edge_agent = edge_agent
        self._cloud_agent = cloud_agent
        self._detector = privacy_detector
        self._sanitizer = sanitizer
        self._edge_available = edge_available

    async def process(
        self,
        query: str,
        session_id: str,
        context_messages: list[dict[str, str]] | None = None,
        raw_query: str | None = None,
    ) -> OrchestratorResult:
        """Process a user query through the full routing pipeline.

        Args:
            query: enriched query (with conversation history and RAG context).
                   Used for complexity analysis and agent execution.
            session_id: current session identifier.
            context_messages: optional conversation history for context.
            raw_query: original user input without enrichment.
                       Used for privacy detection to avoid false positives
                       from conversation history. Defaults to ``query``.
        """
        start_time = time.monotonic()

        # Privacy target: use raw_query to avoid false positives from history
        privacy_target = raw_query if raw_query is not None else query

        # Step 1: Complexity analysis (enriched query gives better context)
        complexity = await analyze_complexity(query, self._edge)

        # Step 2: Privacy detection on RAW user input only
        privacy_result = await self._detector.detect(privacy_target)

        # Step 3: Route based on privacy × complexity
        routing = route(
            privacy_level=privacy_result.level,
            complexity=complexity,
        )

        logger.info(
            "orchestrator_routing",
            session_id=session_id,
            mode=routing.mode.value,
            privacy=privacy_result.level.value,
            complexity=complexity.value,
        )

        # Step 4: Edge-unavailable protection
        # If edge is down and the task would go to edge, or if it's a
        # privacy-sensitive task that should stay local, block cloud access.
        if not self._edge_available and self._requires_local(routing, privacy_result):
            answer = (
                "抱歉，当前本地 LLM 服务不可用，无法安全处理此查询。\n"
                "请先启动本地 LLM 服务（运行 scripts/start_local_llm.bat），然后再试。\n"
                "这是为了保护您的隐私数据不被发送到云端。"
            )
            elapsed_ms = (time.monotonic() - start_time) * 1000
            return OrchestratorResult(
                answer=answer,
                mode=CollaborateMode.DIRECT_LOCAL,
                routing=routing,
                privacy_detection=privacy_result,
                latency_ms=round(elapsed_ms, 1),
            )

        # Step 5: Execute mode
        answer, sanitized_query, mapping = await self._execute_mode(
            mode=routing.mode,
            query=query,
            privacy_result=privacy_result,
            session_id=session_id,
            context_messages=context_messages,
        )

        elapsed_ms = (time.monotonic() - start_time) * 1000

        return OrchestratorResult(
            answer=answer,
            mode=routing.mode,
            routing=routing,
            privacy_detection=privacy_result,
            latency_ms=round(elapsed_ms, 1),
            sanitized_query=sanitized_query,
            restore_mapping=mapping,
        )

    def _requires_local(
        self, routing: RoutingResult, privacy: PrivacyDetection
    ) -> bool:
        """Check if the task must be processed locally."""
        # If routing says local, it must be local
        if routing.mode == CollaborateMode.DIRECT_LOCAL:
            return True
        # If privacy is S3 and we'd need cloud, block it when edge is down
        # (sanitize-cloud requires edge for restore)
        if privacy.level == PrivacyLevel.S3:
            return True
        return False

    async def _execute_mode(
        self,
        mode: CollaborateMode,
        query: str,
        privacy_result: PrivacyDetection,
        session_id: str,
        context_messages: list[dict[str, str]] | None = None,
    ) -> tuple[str, str, dict[str, str]]:
        """Dispatch to the appropriate collaborate mode.

        Returns:
            (answer, sanitized_query, restore_mapping)
        """
        if mode == CollaborateMode.DIRECT_LOCAL:
            answer = await self._mode_direct_local(query, context_messages)
            return answer, query, {}

        elif mode == CollaborateMode.DIRECT_CLOUD:
            answer = await self._mode_direct_cloud(query, context_messages)
            return answer, query, {}

        elif mode == CollaborateMode.SANITIZE_CLOUD:
            answer, sanitized, mapping = await self._mode_sanitize_cloud(
                query, privacy_result, session_id, context_messages
            )
            return answer, sanitized, mapping

        elif mode == CollaborateMode.SKETCH_REFINE:
            answer, sanitized, mapping = await self._mode_sketch_refine(
                query, privacy_result, session_id
            )
            return answer, sanitized, mapping

        else:
            answer = await self._mode_direct_local(query, context_messages)
            return answer, query, {}

    # --- Mode A: Direct Local ---
    async def _mode_direct_local(
        self,
        query: str,
        context_messages: list[dict[str, str]] | None = None,
    ) -> str:
        """Local execution via edge agent — supports tool calls."""
        result = await self._edge_agent.run(query)
        return result.answer

    # --- Mode B: Direct Cloud ---
    async def _mode_direct_cloud(
        self,
        query: str,
        context_messages: list[dict[str, str]] | None = None,
    ) -> str:
        """Cloud execution via cloud agent — supports tool calls."""
        result = await self._cloud_agent.run(query)
        return result.answer

    # --- Mode C: Sanitize → Cloud → Restore ---
    async def _mode_sanitize_cloud(
        self,
        query: str,
        privacy_result: PrivacyDetection,
        session_id: str,
        context_messages: list[dict[str, str]] | None = None,
    ) -> tuple[str, str, dict[str, str]]:
        """Sanitize sensitive data, send to cloud, restore in result.

        Returns:
            (restored_answer, sanitized_query, restore_mapping)
        """
        # Sanitize the query
        sanitize_result = await self._sanitizer.sanitize(
            query, privacy_result.entities, session_id=session_id
        )
        logger.info(
            "sanitized",
            entities_replaced=sanitize_result.entities_replaced,
            original_len=len(query),
            sanitized_len=len(sanitize_result.sanitized_text),
        )

        # Build messages for cloud with sanitized context
        messages: list[LLMMessage] = []
        if context_messages:
            for msg in context_messages:
                messages.append(LLMMessage(
                    role=msg["role"],
                    content=msg["content"],
                ))
        messages.append(LLMMessage(
            role="user",
            content=sanitize_result.sanitized_text,
        ))

        # Cloud inference
        response = await self._cloud.invoke(messages)

        # Restore original values in the response
        restored = await self._sanitizer.restore(
            response.content, sanitize_result.mapping
        )

        return restored, sanitize_result.sanitized_text, sanitize_result.mapping

    # --- Mode D: Sketch-Refine (Reserved) ---
    async def _mode_sketch_refine(
        self,
        query: str,
        privacy_result: PrivacyDetection,
        session_id: str,
    ) -> tuple[str, str, dict[str, str]]:
        """Edge generates a privacy-safe sketch, cloud refines it.

        Reserved for future S3 + complex task handling.
        Currently falls back to sanitize-cloud.

        Returns:
            (restored_answer, sanitized_query, restore_mapping)
        """
        # TODO: Implement proper Sketch-Refine (Mode D)
        # For now, fall back to sanitize-cloud
        logger.info("sketch_refine_fallback", reason="not yet implemented")
        return await self._mode_sanitize_cloud(
            query, privacy_result, session_id, context_messages=None
        )
