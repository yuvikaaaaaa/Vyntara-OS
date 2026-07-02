"""
Intelligence Operating System — FastAPI Dependency Injection
=============================================================
All reusable ``Depends()`` providers are defined here.

Import patterns:

    from app.core.dependencies import (
        get_db,
        get_redis,
        get_current_user,
        require_role,
        PaginationParams,
    )

    @router.get("/items")
    async def list_items(
        db: AsyncSession = Depends(get_db),
        current_user: User = Depends(get_current_user),
        pagination: PaginationParams = Depends(),
    ) -> ...:
        ...

Design constraints:
  - All DB sessions are request-scoped (opened at route start, closed at route end)
  - Clients (Redis, Qdrant, Neo4j) are process-scoped (stored in ``app.state``)
  - Authentication dependencies raise typed ``AuthenticationError`` subclasses
  - Role dependencies raise ``InsufficientPermissionsError``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from neo4j import AsyncDriver
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.constants import (
    PAGINATION_DEFAULT_PAGE_SIZE,
    PAGINATION_MAX_PAGE_SIZE,
    ROLE_ADMIN,
    ROLE_ANALYST,
    ROLE_OPERATOR,
    ROLE_VIEWER,
)
from app.core.exceptions import (
    InsufficientPermissionsError,
    TokenExpiredError,
    TokenInvalidError,
    WebSocketAuthError,
)
from app.core.logging import get_logger
from app.core.security import decode_access_token

logger = get_logger(__name__)

# OAuth2 bearer scheme — used by Swagger UI "Authorize" button
_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Database session (request-scoped)
# ---------------------------------------------------------------------------


async def get_db(request: Request) -> AsyncSession:  # type: ignore[return]
    """
    Yield a request-scoped async SQLAlchemy session.

    The session is committed if the route handler completes without exception,
    or rolled back on any unhandled error.  Always closed at the end.

    Yields:
        ``AsyncSession`` bound to the engine stored in ``app.state``.
    """
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession
    from sqlalchemy.orm import sessionmaker

    engine = request.app.state.db_engine
    # sessionmaker is cheap to create; consider caching on app.state for perf
    factory = sessionmaker(  # type: ignore[call-overload]
        bind=engine,
        class_=_AsyncSession,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Convenient type alias
DbSession = Annotated[AsyncSession, Depends(get_db)]


# ---------------------------------------------------------------------------
# Redis client (process-scoped)
# ---------------------------------------------------------------------------


def get_redis(request: Request) -> aioredis.Redis:
    """
    Return the process-scoped async Redis client stored in ``app.state``.

    Args:
        request: Current FastAPI request.

    Returns:
        ``redis.asyncio.Redis`` instance.
    """
    return request.app.state.redis


RedisClient = Annotated[aioredis.Redis, Depends(get_redis)]


# ---------------------------------------------------------------------------
# Qdrant client (process-scoped)
# ---------------------------------------------------------------------------


def get_qdrant(request: Request) -> AsyncQdrantClient:
    """
    Return the process-scoped Qdrant async client stored in ``app.state``.

    Args:
        request: Current FastAPI request.

    Returns:
        ``AsyncQdrantClient`` instance.
    """
    return request.app.state.qdrant


QdrantClient = Annotated[AsyncQdrantClient, Depends(get_qdrant)]


# ---------------------------------------------------------------------------
# Neo4j driver (process-scoped)
# ---------------------------------------------------------------------------


def get_neo4j(request: Request) -> AsyncDriver:
    """
    Return the process-scoped Neo4j async driver stored in ``app.state``.

    Args:
        request: Current FastAPI request.

    Returns:
        ``neo4j.AsyncDriver`` instance.
    """
    return request.app.state.neo4j


Neo4jDriver = Annotated[AsyncDriver, Depends(get_neo4j)]


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


async def _extract_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """
    Extract the raw JWT string from the ``Authorization: Bearer <token>`` header.

    Raises:
        TokenInvalidError: Header is missing or scheme is not ``Bearer``.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise TokenInvalidError(
            "Missing or invalid Authorization header. "
            "Expected: 'Authorization: Bearer <token>'"
        )
    return credentials.credentials


# ---------------------------------------------------------------------------
# Current-user dependency
# ---------------------------------------------------------------------------


async def get_current_user_payload(
    token: str = Depends(_extract_bearer_token),
) -> dict[str, Any]:
    """
    Validate the JWT and return its decoded payload.

    Args:
        token: Raw JWT string from the Authorization header.

    Returns:
        Decoded JWT payload dictionary.

    Raises:
        TokenExpiredError: Token has expired.
        TokenInvalidError: Token is malformed or signature invalid.
    """
    return decode_access_token(token)


CurrentUserPayload = Annotated[dict[str, Any], Depends(get_current_user_payload)]


async def get_current_user_id(
    payload: CurrentUserPayload,
) -> str:
    """
    Extract the user UUID string from the validated JWT payload.

    Args:
        payload: Decoded JWT payload.

    Returns:
        User UUID string (the ``sub`` claim).

    Raises:
        TokenInvalidError: ``sub`` claim is missing.
    """
    subject = payload.get("sub")
    if not subject:
        raise TokenInvalidError("Token payload missing 'sub' claim.")
    return str(subject)


CurrentUserID = Annotated[str, Depends(get_current_user_id)]


# ---------------------------------------------------------------------------
# Role / permission guards
# ---------------------------------------------------------------------------


def require_role(*allowed_roles: str):
    """
    Factory that returns a FastAPI dependency enforcing role membership.

    Args:
        *allowed_roles: One or more role names (e.g., ``ROLE_ADMIN``, ``ROLE_OPERATOR``).

    Returns:
        An async dependency function raising ``InsufficientPermissionsError``
        if the current user does not hold at least one of the allowed roles.

    Example::

        @router.delete("/{user_id}")
        async def delete_user(
            user_id: UUID,
            _: None = Depends(require_role(ROLE_ADMIN)),
            db: AsyncSession = Depends(get_db),
        ):
            ...
    """
    _allowed = frozenset(allowed_roles)

    async def _guard(payload: CurrentUserPayload) -> None:
        user_roles: list[str] = payload.get("roles", [])
        if not _allowed.intersection(user_roles):
            raise InsufficientPermissionsError(
                f"This action requires one of the following roles: "
                f"{', '.join(sorted(_allowed))}. "
                f"Your roles: {', '.join(user_roles) or 'none'}.",
                details={"required_roles": sorted(_allowed), "user_roles": user_roles},
            )

    return _guard


def require_permission(permission: str):
    """
    Factory that returns a FastAPI dependency enforcing a single permission.

    Permissions are stored in the JWT payload under the ``permissions`` claim.

    Args:
        permission: Permission string (e.g., ``"tools:python:execute"``).

    Returns:
        Async dependency function.

    Example::

        @router.post("/execute")
        async def execute_code(
            _: None = Depends(require_permission("tools:python:execute")),
        ):
            ...
    """

    async def _guard(payload: CurrentUserPayload) -> None:
        user_permissions: list[str] = payload.get("permissions", [])
        if permission not in user_permissions:
            raise InsufficientPermissionsError(
                f"Permission '{permission}' is required for this action.",
                details={"required_permission": permission},
            )

    return _guard


# Pre-built guards for common role patterns
AdminRequired = Depends(require_role(ROLE_ADMIN))
OperatorRequired = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR))
AnalystRequired = Depends(require_role(ROLE_ADMIN, ROLE_OPERATOR, ROLE_ANALYST))
ViewerRequired = Depends(
    require_role(ROLE_ADMIN, ROLE_OPERATOR, ROLE_ANALYST, ROLE_VIEWER)
)


# ---------------------------------------------------------------------------
# WebSocket JWT extraction (no HTTPBearer — tokens passed as query param)
# ---------------------------------------------------------------------------


async def get_ws_user_id(
    token: str = Query(..., description="JWT access token")
) -> str:
    """
    Validate a JWT passed as a query parameter on WebSocket upgrade requests.

    WebSocket connections cannot set custom HTTP headers in the browser, so
    the JWT is passed as ``?token=<jwt>``.

    Args:
        token: Raw JWT string from query parameter.

    Returns:
        User UUID string.

    Raises:
        WebSocketAuthError: Token is expired or invalid.
    """
    try:
        payload = decode_access_token(token)
        subject = payload.get("sub")
        if not subject:
            raise TokenInvalidError("Token missing 'sub' claim.")
        return str(subject)
    except (TokenExpiredError, TokenInvalidError) as exc:
        raise WebSocketAuthError(str(exc)) from exc


WsUserID = Annotated[str, Depends(get_ws_user_id)]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@dataclass
class PaginationParams:
    """
    Common pagination query parameters.

    Attributes:
        page: 1-based page number.
        page_size: Number of items per page (capped at ``max_page_size``).
        offset: Computed SQL offset.
    """

    page: int = Query(default=1, ge=1, description="1-based page number")
    page_size: int = Query(
        default=PAGINATION_DEFAULT_PAGE_SIZE,
        ge=1,
        le=PAGINATION_MAX_PAGE_SIZE,
        alias="page_size",
        description="Items per page",
    )

    @property
    def offset(self) -> int:
        """Compute the SQL OFFSET value from page and page_size."""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """Alias for ``page_size`` — used as SQL LIMIT."""
        return self.page_size


Pagination = Annotated[PaginationParams, Depends(PaginationParams)]


# ---------------------------------------------------------------------------
# Settings dependency (for routes that need config values)
# ---------------------------------------------------------------------------


def get_app_settings():
    """Return the application ``Settings`` singleton."""
    return get_settings()


AppSettings = Annotated[Any, Depends(get_app_settings)]
