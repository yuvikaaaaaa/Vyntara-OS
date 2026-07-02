"""
Intelligence Operating System — Structured Logging
===================================================
Configures ``structlog`` with:
  - JSON rendering in production / text rendering in development
  - Automatic enrichment with timestamp, log level, logger name
  - Request-ID context propagation via ``contextvars``
  - Integration with the stdlib ``logging`` module so that third-party
    libraries (SQLAlchemy, httpx, uvicorn) feed into the same pipeline

Usage in any module::

    from app.core.logging import get_logger

    logger = get_logger(__name__)
    logger.info("thing_happened", user_id=user.id, action="login")

To bind context variables for the duration of a request::

    from app.core.logging import bind_request_context

    bind_request_context(request_id="abc123", user_id="u-1")
    logger.info("request_processed")    # request_id and user_id appear automatically
"""

from __future__ import annotations

import logging
import logging.config
from typing import Any

import structlog
from structlog.types import EventDict, WrappedLogger

# ---------------------------------------------------------------------------
# Custom structlog processors
# ---------------------------------------------------------------------------


def _add_app_context(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Add static application-level fields to every log record."""
    event_dict.setdefault("app", "ios-backend")
    return event_dict


def _drop_color_message_key(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Remove the ``color_message`` key injected by uvicorn's access logger."""
    event_dict.pop("color_message", None)
    return event_dict


def _reorder_keys(
    logger: WrappedLogger,
    method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """
    Ensure ``timestamp``, ``level``, and ``event`` are the first three keys
    for consistent JSON field ordering in log aggregators.
    """
    ordered: dict[str, Any] = {}
    for key in ("timestamp", "level", "event", "app", "request_id"):
        if key in event_dict:
            ordered[key] = event_dict.pop(key)
    ordered.update(event_dict)
    return ordered


# ---------------------------------------------------------------------------
# Configuration entry point
# ---------------------------------------------------------------------------


def configure_logging(
    log_level: str = "INFO",
    log_format: str = "json",
    log_file: str | None = None,
) -> None:
    """
    Configure both stdlib ``logging`` and ``structlog`` for the application.

    This must be called **once** at process startup (inside the FastAPI
    lifespan handler) before any logger is used.

    Args:
        log_level: One of DEBUG | INFO | WARNING | ERROR | CRITICAL.
        log_format: ``"json"`` for machine-readable output (production);
                    ``"text"`` for coloured console output (development).
        log_file: Optional file path. If provided, a ``FileHandler`` is added.
    """
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    # ------------------------------------------------------------------
    # Stdlib logging — handlers
    # ------------------------------------------------------------------
    handlers: dict[str, Any] = {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "plain",
        }
    }
    if log_file:
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": log_file,
            "maxBytes": 100 * 1024 * 1024,  # 100 MB
            "backupCount": 5,
            "formatter": "plain",
            "encoding": "utf-8",
        }

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                # structlog handles actual formatting; stdlib formatter is a pass-through
                "plain": {"format": "%(message)s"}
            },
            "handlers": handlers,
            "loggers": {
                # Root logger — catches everything
                "root": {
                    "level": numeric_level,
                    "handlers": list(handlers.keys()),
                },
                # Reduce noise from chatty third-party loggers
                "uvicorn": {"level": "WARNING", "propagate": True},
                "uvicorn.access": {"level": "WARNING", "propagate": True},
                "sqlalchemy.engine": {
                    "level": "DEBUG" if log_level == "DEBUG" else "WARNING",
                    "propagate": True,
                },
                "httpx": {"level": "WARNING", "propagate": True},
                "httpcore": {"level": "WARNING", "propagate": True},
            },
        }
    )

    # ------------------------------------------------------------------
    # Shared processors (run for every log entry)
    # ------------------------------------------------------------------
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_app_context,
        _drop_color_message_key,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # ------------------------------------------------------------------
    # Renderer — JSON in production, coloured console in development
    # ------------------------------------------------------------------
    if log_format == "json":
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # ------------------------------------------------------------------
    # structlog configuration
    # ------------------------------------------------------------------
    structlog.configure(
        processors=[
            *shared_processors,
            _reorder_keys,
            # Bridge structlog → stdlib so our handlers above receive everything
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Attach the final renderer to the stdlib ProcessorFormatter
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    for handler in logging.root.handlers:
        handler.setFormatter(formatter)


# ---------------------------------------------------------------------------
# Context variable helpers
# ---------------------------------------------------------------------------


def bind_request_context(**kwargs: Any) -> None:
    """
    Bind key-value pairs to the current ``contextvars`` context so that
    every subsequent log call within the same async task automatically
    includes them.

    Typically called in request middleware::

        bind_request_context(request_id="abc", user_id="u-1")

    Args:
        **kwargs: Arbitrary key-value pairs to bind (e.g. request_id, user_id).
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    """
    Clear all contextvars-bound logging context.
    Must be called at the end of each request to prevent context leaking
    across requests in the same asyncio task.
    """
    structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Return a bound structlog logger for the given module name.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A ``BoundLogger`` that emits structured records.

    Example::

        logger = get_logger(__name__)
        logger.info("service_started", port=8000)
    """
    return structlog.get_logger(name)  # type: ignore[return-value]
