"""IOS — BaseService."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import IosBaseException
from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.repositories.unit_of_work import UnitOfWork


class BaseService:
    """
    Shared foundation for all application services.

    Provides:
    - Named structured logger
    - OTel span helper
    - UnitOfWork factory
    - Exception translation (re-raises IosBaseException as-is;
      wraps unexpected exceptions so callers always get typed errors)

    Services are framework-agnostic — no FastAPI, no SQLAlchemy, no HTTP.
    They receive a session factory callable (or AsyncSession) at construction
    to remain independently testable.
    """

    def __init__(self, session_factory: AsyncSession) -> None:
        # session_factory is an AsyncSession instance injected by FastAPI Depends.
        # Named _session to signal it should only be used to create UoW.
        self._session = session_factory
        self._log = get_logger(self.__class__.__module__)

    def _uow(self) -> UnitOfWork:
        """Return a new UnitOfWork bound to the injected session."""
        return UnitOfWork(self._session)

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[UnitOfWork]:
        """Async context manager that yields a committed UoW on clean exit."""
        uow = self._uow()
        async with uow:
            yield uow

    def _span(self, name: str, **attrs):
        """Return an OTel async span context manager."""
        return create_async_span(
            f"{self.__class__.__name__}.{name}",
            attributes={k: str(v) for k, v in attrs.items()},
        )

    def _translate_error(self, exc: Exception) -> IosBaseException:
        """
        Re-raise IosBaseException subclasses as-is;
        wrap unknown exceptions in a generic InfrastructureError.
        """
        if isinstance(exc, IosBaseException):
            return exc
        from app.core.exceptions import InfrastructureError
        return InfrastructureError(
            f"Unexpected error in {self.__class__.__name__}: {exc}",
            details={"original": type(exc).__name__},
        )