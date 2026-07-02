"""
Intelligence Operating System — OpenTelemetry Telemetry
========================================================
Initialises the OpenTelemetry SDK with:
  - OTLP gRPC exporter (to the OTel Collector)
  - Batch span processor for efficiency
  - Auto-instrumentation for FastAPI, SQLAlchemy, Redis, httpx
  - Resource attributes identifying the service

Exposes helpers for manual instrumentation:
  - ``get_tracer()``   — returns a named tracer
  - ``create_span()``  — context manager for manual spans
  - ``trace_async()``  — decorator for async functions

Call ``setup_telemetry()`` once during application startup (lifespan).
"""

from __future__ import annotations

import functools
from contextlib import asynccontextmanager, contextmanager
from typing import Any, AsyncIterator, Callable, Iterator, TypeVar

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import NonRecordingSpan, Span, StatusCode

from app.core.config import get_settings
from app.core.constants import OTEL_SERVICE_NAME
from app.core.logging import get_logger

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Module-level tracer provider (initialised lazily)
_tracer_provider: TracerProvider | None = None
_instrumented: bool = False


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def setup_telemetry(fastapi_app: Any | None = None) -> None:
    """
    Initialise the OpenTelemetry SDK and instrument supported frameworks.

    Must be called **once** at application startup (inside the FastAPI
    lifespan).  Safe to call multiple times — subsequent calls are no-ops.

    Args:
        fastapi_app: Optional FastAPI application instance for
                     ``FastAPIInstrumentor``.  When omitted, FastAPI
                     instrumentation is skipped.
    """
    global _tracer_provider, _instrumented

    settings = get_settings()

    if not settings.otel.enabled:
        logger.info("otel_disabled", reason="OTEL_ENABLED=false")
        _tracer_provider = TracerProvider()  # No-op provider
        trace.set_tracer_provider(_tracer_provider)
        return

    if _instrumented:
        logger.debug("otel_already_configured")
        return

    resource = Resource.create(
        {
            "service.name": settings.otel.service_name,
            "service.version": settings.otel.service_version,
            "deployment.environment": settings.environment,
        }
    )

    provider = TracerProvider(resource=resource)

    # ------------------------------------------------------------------
    # Exporters
    # ------------------------------------------------------------------
    if settings.environment in ("production", "staging"):
        otlp_exporter = OTLPSpanExporter(
            endpoint=settings.otel.exporter_otlp_endpoint,
            insecure=True,  # TLS terminates at the collector in this architecture
        )
        provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
        logger.info(
            "otel_otlp_exporter_configured",
            endpoint=settings.otel.exporter_otlp_endpoint,
        )
    else:
        # Development: also write spans to stdout for easy local debugging
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        logger.info("otel_console_exporter_configured")

    trace.set_tracer_provider(provider)
    _tracer_provider = provider

    # ------------------------------------------------------------------
    # Auto-instrumentation
    # ------------------------------------------------------------------
    SQLAlchemyInstrumentor().instrument(enable_commenter=True)
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()

    if fastapi_app is not None:
        FastAPIInstrumentor.instrument_app(
            fastapi_app,
            excluded_urls="health,health/live,health/ready,metrics",
        )

    _instrumented = True
    logger.info("otel_setup_complete", service=settings.otel.service_name)


def shutdown_telemetry() -> None:
    """
    Flush and shut down the tracer provider.

    Must be called during application shutdown to ensure all buffered
    spans are exported before the process exits.
    """
    global _tracer_provider
    if _tracer_provider is not None:
        _tracer_provider.shutdown()
        logger.info("otel_shutdown_complete")


# ---------------------------------------------------------------------------
# Tracer factory
# ---------------------------------------------------------------------------


def get_tracer(name: str) -> trace.Tracer:
    """
    Return a named OTel tracer.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        An OTel ``Tracer`` instance.  If the SDK is not yet initialised,
        returns a no-op tracer so callers never need to guard against ``None``.
    """
    return trace.get_tracer(name)


# ---------------------------------------------------------------------------
# Synchronous span context manager
# ---------------------------------------------------------------------------


@contextmanager
def create_span(
    name: str,
    *,
    tracer_name: str = OTEL_SERVICE_NAME,
    attributes: dict[str, Any] | None = None,
    record_exception: bool = True,
) -> Iterator[Span]:
    """
    Context manager that creates and manages a synchronous OTel span.

    Args:
        name: Span name (e.g., ``"rag.retrieve"``).
        tracer_name: Named tracer identifier (defaults to service name).
        attributes: Key-value span attributes.
        record_exception: If ``True``, unhandled exceptions are recorded
                          on the span and the span is marked as error.

    Yields:
        Active ``Span`` object.

    Example::

        with create_span("agent.execute", attributes={"agent.type": "research"}) as span:
            result = agent.run(context)
            span.set_attribute("agent.tokens_used", result.tokens_used)
    """
    tracer = get_tracer(tracer_name)
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, _coerce_attribute(value))
        try:
            yield span
        except Exception as exc:
            if record_exception and not isinstance(span, NonRecordingSpan):
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR, str(exc))
            raise


# ---------------------------------------------------------------------------
# Asynchronous span context manager
# ---------------------------------------------------------------------------


@asynccontextmanager
async def create_async_span(
    name: str,
    *,
    tracer_name: str = OTEL_SERVICE_NAME,
    attributes: dict[str, Any] | None = None,
    record_exception: bool = True,
) -> AsyncIterator[Span]:
    """
    Async context manager that creates and manages an OTel span.

    Args:
        name: Span name.
        tracer_name: Named tracer identifier.
        attributes: Span attributes.
        record_exception: Whether to record exceptions on the span.

    Yields:
        Active ``Span`` object.

    Example::

        async with create_async_span("llm.generate") as span:
            tokens = await llm.stream(prompt)
            span.set_attribute("llm.tokens", tokens)
    """
    tracer = get_tracer(tracer_name)
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, _coerce_attribute(value))
        try:
            yield span
        except Exception as exc:
            if record_exception and not isinstance(span, NonRecordingSpan):
                span.record_exception(exc)
                span.set_status(StatusCode.ERROR, str(exc))
            raise


# ---------------------------------------------------------------------------
# Decorator for async functions
# ---------------------------------------------------------------------------


def trace_async(
    span_name: str | None = None,
    *,
    tracer_name: str = OTEL_SERVICE_NAME,
    attributes: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """
    Decorator that wraps an async function in an OTel span.

    Args:
        span_name: Span name override.  Defaults to ``module.function_name``.
        tracer_name: Named tracer identifier.
        attributes: Static span attributes.

    Returns:
        Decorated async function.

    Example::

        @trace_async("rag.embed_batch", attributes={"rag.model": "bge-large"})
        async def embed_batch(texts: list[str]) -> list[list[float]]:
            ...
    """

    def decorator(func: F) -> F:
        effective_name = span_name or f"{func.__module__}.{func.__qualname__}"

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async with create_async_span(
                effective_name,
                tracer_name=tracer_name,
                attributes=attributes,
            ):
                return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def trace_sync(
    span_name: str | None = None,
    *,
    tracer_name: str = OTEL_SERVICE_NAME,
    attributes: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """
    Decorator that wraps a synchronous function in an OTel span.

    Args:
        span_name: Span name override.
        tracer_name: Named tracer identifier.
        attributes: Static span attributes.

    Returns:
        Decorated synchronous function.
    """

    def decorator(func: F) -> F:
        effective_name = span_name or f"{func.__module__}.{func.__qualname__}"

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with create_span(
                effective_name,
                tracer_name=tracer_name,
                attributes=attributes,
            ):
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Attribute coercion
# ---------------------------------------------------------------------------


def _coerce_attribute(value: Any) -> str | int | float | bool | list[Any]:
    """
    Coerce a value to a type accepted by the OTel attribute API.

    OTel attributes must be str, int, float, bool, or a sequence thereof.
    Everything else is stringified.
    """
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_attribute(v) for v in value]
    return str(value)


# ---------------------------------------------------------------------------
# Current span helpers
# ---------------------------------------------------------------------------


def set_span_attribute(key: str, value: Any) -> None:
    """
    Set an attribute on the currently active span (if any).

    Safe to call when no span is active — becomes a no-op.

    Args:
        key: Attribute key.
        value: Attribute value (coerced to OTel-compatible type).
    """
    current_span = trace.get_current_span()
    if not isinstance(current_span, NonRecordingSpan):
        current_span.set_attribute(key, _coerce_attribute(value))


def record_exception_on_span(exc: Exception) -> None:
    """
    Record an exception on the currently active span.

    Args:
        exc: Exception to record.
    """
    current_span = trace.get_current_span()
    if not isinstance(current_span, NonRecordingSpan):
        current_span.record_exception(exc)
        current_span.set_status(StatusCode.ERROR, str(exc))
