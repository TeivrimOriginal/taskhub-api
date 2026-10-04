"""Structured logging with `structlog`.

Two modes:
* local  — coloured, human friendly console renderer;
* prod   — one JSON object per line, ready for Loki/ELK.

Every request is tagged with a `request_id` (bound by `RequestIDMiddleware`), so a
single grep reproduces the whole story of one HTTP call.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.typing import Processor

_CONFIGURED = False


def configure_logging(*, level: str = "INFO", json_logs: bool = False) -> None:
    """Configure stdlib logging + structlog. Safe to call more than once."""
    global _CONFIGURED  # noqa: PLW0603

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
        force=True,
    )
    # Uvicorn ships its own handlers; let them come through structlog instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    renderer: Processor
    if json_logs:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    _CONFIGURED = True


def get_logger(name: str, **initial_values: Any) -> structlog.stdlib.BoundLogger:
    """Return a bound logger; `initial_values` are attached to every record."""
    if not _CONFIGURED:
        configure_logging()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name, **initial_values)
    return logger


def bind_request_context(**values: Any) -> None:
    """Bind values to the current async context so every later log line has them."""
    structlog.contextvars.bind_contextvars(**values)


def clear_request_context() -> None:
    """Reset contextvars (called by the middleware on request completion)."""
    structlog.contextvars.clear_contextvars()
