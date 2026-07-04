"""IOS — Audit Repository."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog, EventLog
from app.core.logging import get_logger

logger = get_logger(__name__)


class AuditRepository:
    """
    Append-only repository for AuditLog and EventLog.

    Does NOT inherit BaseRepository because:
    - AuditLog uses BigInteger PK (not UUID).
    - Both tables are insert-only (no update, no delete, no soft-delete).
    - The generic CRUD operations would be misleading.

    Repositories never commit — UnitOfWork owns the transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # AuditLog
    # ------------------------------------------------------------------

    async def append(self, log: AuditLog) -> AuditLog:
        """Insert one audit record and flush to obtain the generated id."""
        self._session.add(log)
        await self._session.flush()
        await self._session.refresh(log)
        return log

    async def bulk_append(self, logs: list[AuditLog]) -> list[AuditLog]:
        """Insert multiple audit records in a single flush."""
        self._session.add_all(logs)
        await self._session.flush()
        return logs

    async def query(
        self,
        *,
        user_id: UUID | None = None,
        category: str | None = None,
        action: str | None = None,
        resource: str | None = None,
        resource_id: str | None = None,
        outcome: str | None = None,
        ip_address: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[AuditLog], int]:
        """
        Query audit logs with optional filters.

        Returns:
            (records, total_count) for the given filter set.
        """
        filters: list[Any] = []
        if user_id is not None:
            filters.append(AuditLog.user_id == user_id)
        if category is not None:
            filters.append(AuditLog.category == category)
        if action is not None:
            filters.append(AuditLog.action == action)
        if resource is not None:
            filters.append(AuditLog.resource == resource)
        if resource_id is not None:
            filters.append(AuditLog.resource_id == resource_id)
        if outcome is not None:
            filters.append(AuditLog.outcome == outcome)
        if ip_address is not None:
            filters.append(AuditLog.ip_address == ip_address)
        if from_dt is not None:
            filters.append(AuditLog.created_at >= from_dt)
        if to_dt is not None:
            filters.append(AuditLog.created_at <= to_dt)

        where_clause = and_(*filters) if filters else None

        count_stmt = select(func.count()).select_from(AuditLog)
        if where_clause is not None:
            count_stmt = count_stmt.where(where_clause)
        total: int = (await self._session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(AuditLog)
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if where_clause is not None:
            stmt = stmt.where(where_clause)

        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_by_request_id(self, request_id: UUID) -> list[AuditLog]:
        """Return all audit entries sharing a correlation request_id."""
        stmt = (
            select(AuditLog)
            .where(AuditLog.request_id == request_id)
            .order_by(AuditLog.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_failures(
        self,
        user_id: UUID,
        action: str,
        since: datetime,
    ) -> int:
        """Count failed auth attempts for rate-limit and lockout checks."""
        stmt = select(func.count()).select_from(AuditLog).where(
            AuditLog.user_id == user_id,
            AuditLog.action == action,
            AuditLog.outcome == "failure",
            AuditLog.created_at >= since,
        )
        return (await self._session.execute(stmt)).scalar() or 0

    # ------------------------------------------------------------------
    # EventLog
    # ------------------------------------------------------------------

    async def emit(self, event: EventLog) -> EventLog:
        """Insert one event log record and flush."""
        self._session.add(event)
        await self._session.flush()
        await self._session.refresh(event)
        return event

    async def bulk_emit(self, events: list[EventLog]) -> list[EventLog]:
        """Insert multiple event records in a single flush."""
        self._session.add_all(events)
        await self._session.flush()
        return events

    async def query_events(
        self,
        *,
        task_id: UUID | None = None,
        category: str | None = None,
        event_type: str | None = None,
        severity: str | None = None,
        component: str | None = None,
        is_error: bool | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[EventLog], int]:
        """Query event logs with optional filters."""
        filters: list[Any] = []
        if task_id is not None:
            filters.append(EventLog.task_id == task_id)
        if category is not None:
            filters.append(EventLog.category == category)
        if event_type is not None:
            filters.append(EventLog.event_type == event_type)
        if severity is not None:
            filters.append(EventLog.severity == severity)
        if component is not None:
            filters.append(EventLog.component == component)
        if is_error is not None:
            filters.append(EventLog.is_error == is_error)
        if from_dt is not None:
            filters.append(EventLog.created_at >= from_dt)
        if to_dt is not None:
            filters.append(EventLog.created_at <= to_dt)

        where_clause = and_(*filters) if filters else None

        count_stmt = select(func.count()).select_from(EventLog)
        if where_clause is not None:
            count_stmt = count_stmt.where(where_clause)
        total: int = (await self._session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(EventLog)
            .order_by(EventLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if where_clause is not None:
            stmt = stmt.where(where_clause)

        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_error_events(
        self,
        *,
        task_id: UUID | None = None,
        limit: int = 50,
    ) -> list[EventLog]:
        """Return recent ERROR/CRITICAL events, optionally scoped to a task."""
        filters: list[Any] = [EventLog.is_error.is_(True)]
        if task_id is not None:
            filters.append(EventLog.task_id == task_id)
        stmt = (
            select(EventLog)
            .where(and_(*filters))
            .order_by(EventLog.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_events_by_category(
        self,
        from_dt: datetime,
        to_dt: datetime,
    ) -> dict[str, int]:
        """Aggregate event counts by category over a time window."""
        stmt = (
            select(EventLog.category, func.count().label("cnt"))
            .where(
                EventLog.created_at >= from_dt,
                EventLog.created_at <= to_dt,
            )
            .group_by(EventLog.category)
            .order_by(func.count().desc())
        )
        result = await self._session.execute(stmt)
        return {row.category: row.cnt for row in result.all()}