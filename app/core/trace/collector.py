"""Per-request trace collector — taps into structlog without modifying components.

Usage:
    async with trace_context(session_id, query) as collector:
        # All structlog logger.info(...) calls within this block
        # are automatically captured as trace events.
        result = await orchestrator.process(...)
    # trace JSON saved automatically on exit
"""

import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog.contextvars

from app.core.logger.logger import get_logger

logger = get_logger(__name__)

# structlog context key used to store the active collector
COLLECTOR_KEY = "__trace_collector__"


class TraceCollector:
    """Accumulates trace events for a single request.

    Events are added by the structlog processor — component code
    doesn't need any changes.
    """

    def __init__(self, session_id: str, query: str, trace_id: str | None = None) -> None:
        self.trace_id = trace_id or uuid.uuid4().hex[:12]
        self.session_id = session_id
        self.query = query
        self.start_time = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.metadata: dict[str, Any] = {}

    def add_event(self, event: str, data: dict[str, Any]) -> None:
        """Append a trace event with relative timestamp."""
        elapsed_ms = (time.monotonic() - self.start_time) * 1000
        self.events.append({
            "event": event,
            "ts_ms": round(elapsed_ms, 1),
            **data,
        })

    def set_metadata(self, key: str, value: Any) -> None:
        """Attach metadata to the trace (mode, privacy, etc.)."""
        self.metadata[key] = value

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict suitable for JSON output."""
        total_ms = (time.monotonic() - self.start_time) * 1000
        return {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "query": self.query,
            "total_latency_ms": round(total_ms, 1),
            "metadata": self.metadata,
            "event_count": len(self.events),
            "events": self.events,
        }


def _classify_event(event_name: str) -> str:
    """Map structlog event names to trace categories."""
    if "rag" in event_name or "retriever" in event_name:
        return "rag"
    if "privacy" in event_name or "sanitize" in event_name:
        return "privacy"
    if "llm_invoke" in event_name or "llm_stream" in event_name:
        return "llm"
    if "langgraph" in event_name or "agent" in event_name:
        return "agent"
    if "orchestrator" in event_name or "routing" in event_name:
        return "orchestrator"
    if "enrich" in event_name or "conversation" in event_name:
        return "context"
    return "other"


def trace_processor(
    logger: Any, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Structlog processor that feeds events to the active TraceCollector.

    This processor is a no-op when no collector is bound to the
    current context, so it has zero overhead for non-traced requests.

    IMPORTANT: Never mutate event_dict — it is shared with downstream
    processors (ConsoleRenderer). Copy fields instead.
    """
    try:
        collector: TraceCollector | None = structlog.contextvars.get_contextvars().get(
            COLLECTOR_KEY
        )
    except Exception:
        collector = None

    if collector is not None:
        event_name = event_dict.get("event", "")
        # Build a clean copy for the trace — exclude fields that are
        # either internal (collector ref, log_level) or redundant (timestamp).
        trace_data = {
            "category": _classify_event(event_name),
            **{
                k: v
                for k, v in event_dict.items()
                if k not in ("event", "timestamp", "log_level", COLLECTOR_KEY)
            },
        }
        collector.add_event(event=event_name, data=trace_data)

    # Always return event_dict unchanged so ConsoleRenderer gets the full data
    return event_dict


@asynccontextmanager
async def trace_context(
    session_id: str,
    query: str,
    trace_id: str | None = None,
):
    """Context manager that enables trace collection for the current async task.

    Usage:
        async with trace_context(session_id, query) as collector:
            ...
        # collector.saved_path contains the file path after exit
    """
    collector = TraceCollector(session_id, query, trace_id)

    # Bind collector to structlog context for this async scope
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(**{COLLECTOR_KEY: collector})

    try:
        yield collector
    finally:
        structlog.contextvars.unbind_contextvars(COLLECTOR_KEY)


def save_trace(collector: TraceCollector, output_dir: str) -> str | None:
    """Persist a trace collector to a JSON file.

    Args:
        collector: the completed TraceCollector.
        output_dir: directory to write the JSON file.

    Returns:
        Path to the saved file, or None if saving failed.
    """
    try:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        filename = f"{collector.trace_id}_{int(time.time())}.json"
        filepath = out_path / filename

        data = collector.to_dict()
        filepath.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("trace_saved", path=str(filepath), events=len(collector.events))
        return str(filepath)
    except Exception as exc:
        logger.warning("trace_save_failed", error=str(exc))
        return None
