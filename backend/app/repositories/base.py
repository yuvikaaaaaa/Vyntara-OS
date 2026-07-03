"""IOS — Generic BaseRepository."""
from __future__ import annotations

from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import Select, and_, delete, func, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.database.base import Base

T = TypeVar("T", bound=Base)
logger = get_logger(__name__)


class BaseRepository(Generic[T]):
    """
    Generic async repository providing standard persistence operations.

    Domain repositories inherit from this class and add domain-specific
    queries.  No business logic, no validation, no HTTP concepts.

    Repositories NEVER commit.  The Unit of Work owns the transaction.
    """

    model: type[T]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    async def create(self, obj: T) -> T:
        """Persist a new ORM instance and flush to obtain DB-generated values."""
        async with create_async_span(f"{self.__class__.__name__}.create"):
            self._session.add(obj)
            await self._session.flush()
            await self._session.refresh(obj)
            return obj

    async def get_by_id(self, id: UUID, *, raise_not_found: bool = False) -> T | None:
        """Return the record with the given primary key UUID, or None."""
        async with create_async_span(f"{self.__class__.__name__}.get_by_id"):
            result = await self._session.get(self.model, id)
            if result is None and raise_not_found:
                raise NotFoundError(
                    f"{self.model.__name__} with id={id} not found.",
                    details={"id": str(id)},
                )
            return result

    async def get_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        order_by: InstrumentedAttribute | None = None,
        descending: bool = False,
    ) -> list[T]:
        """Return all records with optional ordering and pagination."""
        stmt = self._base_select()
        stmt = self._apply_order(stmt, order_by, descending)
        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update(self, obj: T, data: dict[str, Any]) -> T:
        """Apply a dict of field updates to an ORM instance and flush."""
        async with create_async_span(f"{self.__class__.__name__}.update"):
            for key, value in data.items():
                setattr(obj, key, value)
            await self._session.flush()
            await self._session.refresh(obj)
            return obj

    async def delete(self, obj: T) -> None:
        """Physically delete an ORM instance."""
        async with create_async_span(f"{self.__class__.__name__}.delete"):
            await self._session.delete(obj)
            await self._session.flush()

    async def soft_delete(self, obj: T) -> T:
        """
        Soft-delete via AuditMixin.soft_delete().

        The model must have a ``deleted_at`` column (AuditMixin).
        """
        async with create_async_span(f"{self.__class__.__name__}.soft_delete"):
            obj.soft_delete()  # type: ignore[attr-defined]
            await self._session.flush()
            return obj

    async def restore(self, obj: T) -> T:
        """Restore a soft-deleted record (clears deleted_at)."""
        obj.restore()  # type: ignore[attr-defined]
        await self._session.flush()
        return obj

    # ------------------------------------------------------------------
    # Existence / counting
    # ------------------------------------------------------------------

    async def exists(self, id: UUID) -> bool:
        """Return True if a record with the given id exists (not soft-deleted)."""
        stmt = select(func.count()).select_from(self.model).where(
            self.model.id == id  # type: ignore[attr-defined]
        )
        if self._has_soft_delete():
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        result = await self._session.execute(stmt)
        return (result.scalar() or 0) > 0

    async def count(self, filters: list[Any] | None = None) -> int:
        """Return the total number of (non-deleted) records matching optional filters."""
        stmt = select(func.count()).select_from(self.model)
        if self._has_soft_delete():
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        if filters:
            stmt = stmt.where(and_(*filters))
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    async def paginate(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        filters: list[Any] | None = None,
        order_by: InstrumentedAttribute | None = None,
        descending: bool = False,
    ) -> tuple[list[T], int]:
        """
        Return a page of records and the total matching count.

        Returns:
            (items, total) where total is the count before pagination.
        """
        async with create_async_span(f"{self.__class__.__name__}.paginate"):
            where = self._soft_delete_filter()
            if filters:
                where = where + list(filters)

            total_stmt = select(func.count()).select_from(self.model)
            if where:
                total_stmt = total_stmt.where(and_(*where))
            total: int = (await self._session.execute(total_stmt)).scalar() or 0

            stmt = self._base_select()
            if where:
                stmt = stmt.where(and_(*where))
            stmt = self._apply_order(stmt, order_by, descending)
            stmt = stmt.offset((page - 1) * page_size).limit(page_size)

            result = await self._session.execute(stmt)
            return list(result.scalars().all()), total

    # ------------------------------------------------------------------
    # Filter queries
    # ------------------------------------------------------------------

    async def list_by_filters(
        self,
        filters: list[Any],
        *,
        limit: int = 100,
        offset: int = 0,
        order_by: InstrumentedAttribute | None = None,
        descending: bool = False,
    ) -> list[T]:
        """Return records matching all supplied SQLAlchemy filter expressions."""
        where = self._soft_delete_filter() + list(filters)
        stmt = self._base_select().where(and_(*where))
        stmt = self._apply_order(stmt, order_by, descending)
        stmt = stmt.offset(offset).limit(limit)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_field(
        self,
        field: InstrumentedAttribute,
        value: Any,
    ) -> T | None:
        """Return the first record where ``field == value`` (soft-delete aware)."""
        where = self._soft_delete_filter() + [field == value]
        stmt = self._base_select().where(and_(*where)).limit(1)
        result = await self._session.execute(stmt)
        return result.scalars().first()

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    async def bulk_create(self, objects: list[T]) -> list[T]:
        """Add multiple ORM instances in one flush."""
        async with create_async_span(f"{self.__class__.__name__}.bulk_create"):
            self._session.add_all(objects)
            await self._session.flush()
            for obj in objects:
                await self._session.refresh(obj)
            return objects

    async def bulk_update(
        self,
        filters: list[Any],
        values: dict[str, Any],
    ) -> int:
        """
        UPDATE … SET values WHERE filters.

        Returns:
            Number of rows affected.
        """
        async with create_async_span(f"{self.__class__.__name__}.bulk_update"):
            stmt = (
                update(self.model)
                .where(and_(*filters))
                .values(**values)
                .execution_options(synchronize_session="fetch")
            )
            result = await self._session.execute(stmt)
            await self._session.flush()
            return result.rowcount  # type: ignore[return-value]

    async def bulk_delete(self, filters: list[Any]) -> int:
        """
        DELETE WHERE filters (physical delete).

        Returns:
            Number of rows deleted.
        """
        async with create_async_span(f"{self.__class__.__name__}.bulk_delete"):
            stmt = (
                delete(self.model)
                .where(and_(*filters))
                .execution_options(synchronize_session="fetch")
            )
            result = await self._session.execute(stmt)
            await self._session.flush()
            return result.rowcount  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Session utilities
    # ------------------------------------------------------------------

    async def refresh(self, obj: T) -> T:
        """Reload all attributes of an ORM instance from the database."""
        await self._session.refresh(obj)
        return obj

    async def flush(self) -> None:
        """Flush pending SQL to the database without committing."""
        await self._session.flush()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_select(self) -> Select:
        return select(self.model)

    def _has_soft_delete(self) -> bool:
        mapper = inspect(self.model)
        return "deleted_at" in {c.key for c in mapper.columns}

    def _soft_delete_filter(self) -> list[Any]:
        if self._has_soft_delete():
            return [self.model.deleted_at.is_(None)]  # type: ignore[attr-defined]
        return []

    @staticmethod
    def _apply_order(
        stmt: Select,
        order_by: InstrumentedAttribute | None,
        descending: bool,
    ) -> Select:
        if order_by is not None:
            stmt = stmt.order_by(order_by.desc() if descending else order_by.asc())
        return stmt