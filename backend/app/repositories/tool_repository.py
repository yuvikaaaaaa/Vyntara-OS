"""IOS — Tool Repository."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import and_, func, select, update

from app.core.enums import ToolName, ToolStatus
from app.models.tool import Tool, ToolExecution, ToolResult
from app.repositories.base import BaseRepository


class ToolRepository(BaseRepository[Tool]):
    model = Tool

    # ------------------------------------------------------------------
    # Tool (Registry)
    # ------------------------------------------------------------------

    async def get_by_name(self, tool_name: ToolName) -> Tool | None:
        stmt = select(Tool).where(
            Tool.tool_name == tool_name,
            Tool.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_enabled(self) -> list[Tool]:
        stmt = (
            select(Tool)
            .where(
                Tool.is_enabled.is_(True),
                Tool.deleted_at.is_(None),
            )
            .order_by(Tool.tool_name.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_stats(
        self,
        tool_id: UUID,
        *,
        success: bool,
        latency_ms: int,
    ) -> None:
        """Update running success/failure counts and exponential moving avg latency."""
        stmt = (
            update(Tool)
            .where(Tool.id == tool_id)
            .values(
                total_executions=Tool.total_executions + 1,
                success_count=Tool.success_count + (1 if success else 0),
                failure_count=Tool.failure_count + (0 if success else 1),
                # EMA with α=0.1
                avg_latency_ms=(
                    Tool.avg_latency_ms * 0.9 + latency_ms * 0.1
                ),
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    # ------------------------------------------------------------------
    # ToolExecution
    # ------------------------------------------------------------------

    async def create_execution(self, execution: ToolExecution) -> ToolExecution:
        self._session.add(execution)
        await self._session.flush()
        await self._session.refresh(execution)
        return execution

    async def get_execution_by_id(
        self, execution_id: UUID
    ) -> ToolExecution | None:
        return await self._session.get(ToolExecution, execution_id)

    async def list_executions_for_task(
        self,
        task_id: UUID,
        *,
        tool_name: ToolName | None = None,
        status: ToolStatus | None = None,
    ) -> list[ToolExecution]:
        filters: list = [ToolExecution.task_id == task_id]
        if tool_name:
            filters.append(ToolExecution.tool_name == tool_name)
        if status:
            filters.append(ToolExecution.status == status)
        stmt = (
            select(ToolExecution)
            .where(and_(*filters))
            .order_by(ToolExecution.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_execution_status(
        self,
        execution_id: UUID,
        status: ToolStatus,
        extra: dict | None = None,
    ) -> None:
        from datetime import datetime, timezone
        values: dict = {"status": status}
        if status == ToolStatus.RUNNING:
            values["started_at"] = datetime.now(tz=timezone.utc)
        if status in (ToolStatus.COMPLETE, ToolStatus.FAILED, ToolStatus.TIMED_OUT):
            completed_at = datetime.now(tz=timezone.utc)
            values["completed_at"] = completed_at
        if extra:
            values.update(extra)
        stmt = (
            update(ToolExecution)
            .where(ToolExecution.id == execution_id)
            .values(**values)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_sandbox_violations(
        self, task_id: UUID
    ) -> list[ToolExecution]:
        stmt = select(ToolExecution).where(
            ToolExecution.task_id == task_id,
            ToolExecution.sandbox_violation.is_(True),
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_executions_by_tool(
        self, task_id: UUID
    ) -> dict[str, int]:
        stmt = (
            select(ToolExecution.tool_name, func.count().label("cnt"))
            .where(ToolExecution.task_id == task_id)
            .group_by(ToolExecution.tool_name)
        )
        result = await self._session.execute(stmt)
        return {row.tool_name: row.cnt for row in result.all()}

    # ------------------------------------------------------------------
    # ToolResult
    # ------------------------------------------------------------------

    async def create_result(self, result_obj: ToolResult) -> ToolResult:
        self._session.add(result_obj)
        await self._session.flush()
        await self._session.refresh(result_obj)
        return result_obj

    async def get_result_for_execution(
        self, execution_id: UUID
    ) -> ToolResult | None:
        stmt = select(ToolResult).where(
            ToolResult.execution_id == execution_id
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()