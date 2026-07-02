"""
Intelligence Operating System — Middleware Stack
================================================
Registers all Starlette/FastAPI middleware and exception handlers.

Middleware execution order (outermost first):
  1. TrustedHostMiddleware     — rejects requests with invalid Host headers
  2. CORSMiddleware            — sets CORS response headers
  3. RequestIDMiddleware       — assigns X-Request-ID to every request
  4. AccessLogMiddleware       — structured JSON access log per request
  5. (FastAPI route handling)
  6. Global exception handler  — converts IosBaseException → RFC 7807 JSON

Call ``register_middleware(app)`` and ``register_exception_handlers(app)``
from ``main.py`` during application construction.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.core.config import get_settings
from app.core.constants import LOG_CORRELATION_ID_HEADER, LOG_REQUEST_ID_HEADER
from app.core.exceptions import IosBaseException
from app.core.logging import bind_request_context, clear_request_context, get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Request-ID Middleware
# ---------------------------------------------------------------------------


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Assigns a unique ``X-Request-ID`` header to every request.

    If the incoming request already carries an ``X-Request-ID`` header it is
    used as-is (supports client-side request tracing); otherwise a new UUIDv4
    is generated.

    The ID is:
    - Added to the logging context (appears in all log lines for the request)
    - Echoed back in the response ``X-Request-ID`` header
    - Added to the OpenTelemetry span attributes
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get(LOG_REQUEST_ID_HEADER) or str(uuid.uuid4())
        correlation_id = request.headers.get(LOG_CORRELATION_ID_HEADER, request_id)

        # Bind to contextvars so all loggers within this request see these fields
        bind_request_context(
            request_id=request_id,
            correlation_id=correlation_id,
        )

        # Store on request state for use by downstream dependencies
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id

        try:
            response = await call_next(request)
        finally:
            clear_request_context()

        response.headers[LOG_REQUEST_ID_HEADER] = request_id
        response.headers[LOG_CORRELATION_ID_HEADER] = correlation_id
        return response


# ---------------------------------------------------------------------------
# Access Log Middleware
# ---------------------------------------------------------------------------


class AccessLogMiddleware(BaseHTTPMiddleware):
    """
    Emits a structured JSON access log record for every HTTP request.

    Skips health-check endpoints to avoid log noise.
    Records: method, path, status_code, duration_ms, client_ip, user_agent.
    """

    _SKIP_PATHS: frozenset[str] = frozenset(
        {"/health", "/health/live", "/health/ready", "/metrics"}
    )

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.url.path in self._SKIP_PATHS:
            return await call_next(request)

        start_time = time.perf_counter()
        client_ip = _get_client_ip(request)

        response: Response
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(
                "request_error",
                method=request.method,
                path=request.url.path,
                client_ip=client_ip,
                duration_ms=duration_ms,
                exc=str(exc),
            )
            raise

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        log_fn = logger.warning if response.status_code >= 400 else logger.info
        log_fn(
            "request_handled",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_ip=client_ip,
            user_agent=request.headers.get("user-agent", ""),
            content_length=response.headers.get("content-length", ""),
        )
        return response


# ---------------------------------------------------------------------------
# Registration helpers
# ---------------------------------------------------------------------------


def register_middleware(app: FastAPI) -> None:
    """
    Register all middleware on the FastAPI application.

    Middleware is added in reverse execution order due to how Starlette's
    middleware stack works (last-added runs outermost).

    Args:
        app: FastAPI application instance.
    """
    settings = get_settings()

    # Access logging (innermost custom middleware)
    app.add_middleware(AccessLogMiddleware)

    # Request-ID injection
    app.add_middleware(RequestIDMiddleware)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            LOG_REQUEST_ID_HEADER,
            LOG_CORRELATION_ID_HEADER,
            "Accept",
            "Accept-Language",
        ],
        expose_headers=[LOG_REQUEST_ID_HEADER, LOG_CORRELATION_ID_HEADER],
        max_age=600,
    )

    # Trusted host (outermost)
    if settings.allowed_hosts and settings.allowed_hosts != ["*"]:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=settings.allowed_hosts,
        )

    logger.debug("middleware_registered")


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register global exception handlers that convert exceptions to
    RFC 7807-compliant ``application/problem+json`` responses.

    Args:
        app: FastAPI application instance.
    """

    @app.exception_handler(IosBaseException)
    async def ios_exception_handler(
        request: Request, exc: IosBaseException
    ) -> JSONResponse:
        """Map domain exceptions to structured JSON error responses."""
        request_id = getattr(request.state, "request_id", None)
        log_fn = logger.error if exc.http_status >= 500 else logger.warning
        log_fn(
            "ios_exception",
            code=exc.code,
            message=exc.message,
            status=exc.http_status,
            path=request.url.path,
        )
        body: dict[str, Any] = {
            "type": f"https://ios.internal/errors/{exc.code.lower()}",
            "title": exc.code,
            "status": exc.http_status,
            "detail": exc.message,
            "instance": request.url.path,
        }
        if exc.details:
            body["extensions"] = exc.details
        if request_id:
            body["request_id"] = request_id
        return JSONResponse(
            status_code=exc.http_status,
            content=body,
            media_type="application/problem+json",
        )

    @app.exception_handler(PydanticValidationError)
    async def pydantic_validation_handler(
        request: Request, exc: PydanticValidationError
    ) -> JSONResponse:
        """Convert Pydantic v2 validation errors to 422 JSON responses."""
        logger.warning(
            "pydantic_validation_error",
            path=request.url.path,
            errors=exc.error_count(),
        )
        return JSONResponse(
            status_code=422,
            content={
                "type": "https://ios.internal/errors/validation_error",
                "title": "VALIDATION_ERROR",
                "status": 422,
                "detail": "Request body validation failed.",
                "errors": exc.errors(include_url=False),
                "instance": request.url.path,
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Catch-all for unhandled exceptions — prevents leaking stack traces."""
        logger.exception(
            "unhandled_exception",
            path=request.url.path,
            exc_type=type(exc).__name__,
            exc=str(exc),
        )
        request_id = getattr(request.state, "request_id", None)
        body: dict[str, Any] = {
            "type": "https://ios.internal/errors/internal_error",
            "title": "INTERNAL_ERROR",
            "status": 500,
            "detail": "An unexpected error occurred. Our team has been notified.",
            "instance": request.url.path,
        }
        if request_id:
            body["request_id"] = request_id
        return JSONResponse(
            status_code=500,
            content=body,
            media_type="application/problem+json",
        )

    logger.debug("exception_handlers_registered")


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _get_client_ip(request: Request) -> str:
    """
    Extract the real client IP, respecting X-Forwarded-For from NGINX.

    Args:
        request: Incoming Starlette request.

    Returns:
        Client IP string.
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # X-Forwarded-For: client, proxy1, proxy2
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
