"""
Intelligence Operating System — Async Database Session Management
================================================================
Provides:

``AsyncSessionFactory``
    A callable that returns ``AsyncSession`` instances.  Constructed once
    from the shared engine and stored for the process lifetime.

``get_db_session()``
    Async generator used as a FastAPI dependency and in background tasks.
    Commits on clean exit; rolls back on exception; always closes.

``transactional``
    Decorator for service-layer methods that must run inside a single
    transaction.  Injects the session via a keyword argument if the caller
    did not provide one.

``run_in_transaction``
    Async context manager for ad-hoc transaction blocks.

All sessions are ``expire_on_commit=False`` — this prevents "detached
instance" errors when Pydantic serialises ORM objects after a commit.
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Callable, TypeVar

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.exceptions import DatabaseConnectionError, DatabaseQueryError
from app.core.logging import get_logger
from app.core.telemetry import create_async_span

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Module-level session factory (initialised lazily on first call)
# ---------------------------------------------------------------------------

_session_factory: async_sessionmaker[AsyncSession] | None = None
_engine: AsyncEngine | None = None


def _build_engine(settings: Any = None) -> AsyncEngine:
    """
    Construct the async SQLAlchemy engine from application settings.

    Args:
        settings: Optional pre-loaded ``Settings`` instance.  If ``None``,
                  ``get_settings()`` is called.

    Returns:
        Configured ``AsyncEngine``.
    """
    cfg = settings or get_settings()
    engine = create_async_engine(
        cfg.db.async_url,
        pool_size=cfg.db.pool_size,
        max_overflow=cfg.db.max_overflow,
        pool_timeout=cfg.db.pool_timeout,
        pool_recycle=cfg.db.pool_recycle,
        pool_pre_ping=True,          # Detect stale connections before use
        echo=cfg.db.echo_sql,
        future=True,
        # Connection arguments for asyncpg
        connect_args={
            "server_settings": {
                "application_name": "ios-backend",
                "jit": "off",    # Disable JIT for short OLTP queries
            }
        },
    )
    logger.info(
        "db_engine_built",
        host=cfg.db.host,
        port=cfg.db.port,
        db=cfg.db.db,
        pool_size=cfg.db.pool_size,
        max_overflow=cfg.db.max_overflow,
    )
    return engine


def get_engine(settings: Any = None) -> AsyncEngine:
    """
    Return the singleton ``AsyncEngine``, creating it on first call.

    Thread-safe for asyncio single-thread model.  Not safe for multi-process
    without forking — call after the fork in that case.

    Args:
        settings: Optional ``Settings`` override (useful in tests).

    Returns:
        Shared ``AsyncEngine`` instance.
    """
    global _engine
    if _engine is None:
        _engine = _build_engine(settings)
    return _engine


def get_session_factory(settings: Any = None) -> async_sessionmaker[AsyncSession]:
    """
    Return the singleton ``async_sessionmaker``, creating it on first call.

    Args:
        settings: Optional ``Settings`` override.

    Returns:
        Configured ``async_sessionmaker``.
    """
    global _session_factory
    if _session_factory is None:
        engine = get_engine(settings)
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
        logger.debug("db_session_factory_created")
    return _session_factory


def reset_session_factory() -> None:
    """
    Reset the cached engine and session factory.

    **Testing only** — allows each test to inject a fresh engine backed by a
    test database (or an in-memory SQLite for unit tests that do not require
    PostgreSQL).
    """
    global _engine, _session_factory
    _engine = None
    _session_factory = None


# ---------------------------------------------------------------------------
# Async generator dependency (FastAPI Depends + background tasks)
# ---------------------------------------------------------------------------


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """
    Async generator yielding a request-scoped ``AsyncSession``.

    Behaviour:
    - Opens a new session from the shared factory.
    - Yields the session to the caller.
    - Commits on clean exit.
    - Rolls back on **any** unhandled exception, then re-raises.
    - Always closes the session in the ``finally`` block.

    FastAPI usage::

        @router.post("/items")
        async def create_item(
            db: AsyncSession = Depends(get_db_session),
        ) -> ItemResponse:
            ...

    Background task usage::

        async with contextlib.asynccontextmanager(get_db_session)() as db:
            await repository.save(record, db=db)

    Yields:
        An open ``AsyncSession``.

    Raises:
        DatabaseQueryError: Wraps ``SQLAlchemyError`` raised during commit.
        DatabaseConnectionError: Wraps connection-level failures.
    """
    factory = get_session_factory()
    session: AsyncSession = factory()

    async with create_async_span(
        "db.session",
        attributes={"db.system": "postgresql"},
    ):
        try:
            yield session
            await session.commit()
            logger.debug("db_session_committed")
        except SQLAlchemyError as exc:
            await session.rollback()
            logger.error(
                "db_session_rollback",
                exc_type=type(exc).__name__,
                exc=str(exc),
            )
            # Classify by exception type
            exc_str = str(exc).lower()
            if "connection" in exc_str or "connect" in exc_str:
                raise DatabaseConnectionError(
                    f"Database connection error: {exc}",
                    details={"original_error": type(exc).__name__},
                ) from exc
            raise DatabaseQueryError(
                f"Database query error: {exc}",
                details={"original_error": type(exc).__name__},
            ) from exc
        except Exception:
            await session.rollback()
            logger.error("db_session_rollback_unexpected")
            raise
        finally:
            await session.close()
            logger.debug("db_session_closed")


# ---------------------------------------------------------------------------
# Async context manager for ad-hoc transaction blocks
# ---------------------------------------------------------------------------


@asynccontextmanager
async def run_in_transaction(
    session: AsyncSession | None = None,
) -> AsyncIterator[AsyncSession]:
    """
    Async context manager that runs a block inside a database transaction.

    If a session is provided it is used directly (the caller is responsible
    for committing).  If ``None``, a new session is obtained from the factory
    and managed for the duration of the block.

    Args:
        session: Optional existing ``AsyncSession``.

    Yields:
        The active ``AsyncSession``.

    Raises:
        DatabaseQueryError: On SQLAlchemy errors during the block.

    Example::

        async with run_in_transaction() as db:
            await db.execute(insert(User).values(**user_data))
            await db.execute(insert(AuditLog).values(**audit_data))
        # Committed automatically on block exit
    """
    if session is not None:
        # Caller owns the session — no commit/rollback here
        yield session
        return

    async for db in get_db_session():
        yield db


# ---------------------------------------------------------------------------
# @transactional service-layer decorator
# ---------------------------------------------------------------------------


def transactional(func: F) -> F:
    """
    Decorator that ensures the wrapped async method executes within a
    database transaction, injecting an ``AsyncSession`` as ``db`` keyword
    argument if one was not already supplied.

    Intended for service-layer methods::

        class TaskService:
            @transactional
            async def create_task(
                self,
                task_data: TaskCreate,
                db: AsyncSession | None = None,
            ) -> Task:
                # db is guaranteed to be an AsyncSession here
                self._repo.save(task, db=db)

    If the caller already supplies ``db``, that session is used and the
    decorator acts as a pass-through (avoids nested transaction wrapping).

    Args:
        func: Async method to wrap.  Must accept ``db`` as a keyword argument.

    Returns:
        Wrapped async function.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("db") is not None:
            # Session already provided — pass through
            return await func(*args, **kwargs)

        async for session in get_db_session():
            kwargs["db"] = session
            return await func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Health-check ping
# ---------------------------------------------------------------------------


async def ping_database() -> bool:
    """
    Execute a minimal query to verify database connectivity.

    Used by the health-check endpoint and the startup liveness probe.

    Returns:
        ``True`` if the database responds, ``False`` otherwise.
    """
    from sqlalchemy import text

    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.warning("db_ping_failed", exc=str(exc))
        return False


# ---------------------------------------------------------------------------
# Graceful engine shutdown
# ---------------------------------------------------------------------------


async def dispose_engine() -> None:
    """
    Dispose the shared SQLAlchemy engine, closing all pooled connections.

    Must be called during application shutdown (inside the FastAPI lifespan
    teardown) to allow the database server to reclaim connections.
    """
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("db_engine_disposed")