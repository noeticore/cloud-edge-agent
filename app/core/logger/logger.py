"""Structured logging configuration using structlog."""

import logging
import sys

import structlog

from app.core.config.settings import LogLevel


def setup_logging(level: LogLevel = LogLevel.INFO) -> None:
    """Configure structlog + stdlib logging for the application."""
    from app.core.trace.collector import COLLECTOR_KEY, trace_processor

    # Merge context vars, then immediately strip the trace collector ref
    # so it never leaks into console output.
    def _strip_trace_ref(
        logger: object, method_name: str, event_dict: dict
    ) -> dict:
        event_dict.pop(COLLECTOR_KEY, None)
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _strip_trace_ref,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            trace_processor,
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.value)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a named logger instance."""
    return structlog.get_logger(name)
