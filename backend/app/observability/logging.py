"""
logging.py — Structured logging configuration.

Sets up structlog with:
  - JSON output in production (machine-parseable for log aggregators)
  - Console output in development (human-readable, colored)
  - Log level from ENVIRONMENT (debug in dev, info in production)
  - Request ID / session ID injection when available
  - Timestamp, log level, logger name in every entry

Call configure_logging() once at app startup (in main.py lifespan).
"""
from __future__ import annotations

import logging
import sys

import structlog

from app.config import get_settings


def configure_logging() -> None:
    """
    Configure structlog and stdlib logging for the application.
    Must be called once at startup, before any log calls.
    """
    settings = get_settings()
    is_debug = settings.DEBUG or settings.ENVIRONMENT == "development"
    log_level = logging.DEBUG if is_debug else logging.INFO

    # Shared processors for both structlog and stdlib
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_debug:
        # Development: colored console output
        renderer = structlog.dev.ConsoleRenderer(colors=True)
    else:
        # Production: JSON lines (one JSON object per log line)
        # Parseable by ELK, CloudWatch, Datadog, etc.
        shared_processors.append(structlog.processors.format_exc_info)
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging (catches uvicorn, sqlalchemy, etc.)
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Suppress noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if is_debug else logging.WARNING
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
