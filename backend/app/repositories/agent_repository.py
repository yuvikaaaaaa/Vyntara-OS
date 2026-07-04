"""IOS — Agent Repository."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update

from app.core.enums import AgentType, TaskStatus, WorkflowType
from app.models.agent import AgentExecution, AgentTask
from app.repositories.base import BaseRepository


class AgentRepository(BaseRepository[AgentTask]):
    model = AgentTask

    # ------------------------------------------------------------------
    # AgentTask
    # ------------------------------------------------------------------

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        status: TaskStatus | None = None,
        workflow_type: WorkflowType | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AgentTask], int]:
        filters = [AgentTask.user_id == user_id]
        if status:
            filters.append(AgentTask.status == status)
        if workflow_type:
            filters.append(AgentTask.workflow_type == workflow_type)
        return await self.paginate(
            page=page,
            page_size=page_size,
            filters=filters,
            order_by=AgentTask.created_at,
            descending=True,
        )

    async def get_active_tasks(self, user_id: UUID) -> list[AgentTask]:
        active_statuses = [
            TaskStatus.PENDING,
            TaskStatus.PLANNING,
            TaskStatus.EXECUTING,
            TaskStatus.AWAITING_APPROVAL,
        ]
        stmt = (
            select(AgentTask)
            .where(
                AgentTask.user_id == user_id,
                AgentTask.status.in_(active_statuses),
                AgentTask.deleted_at.is_(None),
            )
            .order_by(AgentTask.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self,
        task_id: UUID,
        status: TaskStatus,
        *,
        extra: dict | None = None,
    ) -> None:
        values: dict = {"status": status}
        if status == TaskStatus.EXECUTING and not extra:
            values["started_at"] = datetime.now(tz=timezone.utc)
        if status in (TaskStatus.COMPLETE, TaskStatus.FAILED, TaskStatus.CANCELLED):
            values["completed_at"] = datetime.now(tz=timezone.utc)
        if extra:
            values.update(extra)
        stmt = (
            update(AgentTask)
            .where(AgentTask.id == task_id)
            .values(**values)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def accumulate_tokens(
        self,
        task_id: UUID,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        stmt = (
            update(AgentTask)
            .where(AgentTask.id == task_id)
            .values(
                total_prompt_tokens=AgentTask.total_prompt_tokens + prompt_tokens,
                total_completion_tokens=AgentTask.total_completion_tokens + completion_tokens,
                total_tokens=AgentTask.total_tokens + prompt_tokens + completion_tokens,
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def get_tasks_by_conversation(
        self, conversation_id: UUID
    ) -> list[AgentTask]:
        stmt = (
            select(AgentTask)
            .where(
                AgentTask.conversation_id == conversation_id,
                AgentTask.deleted_at.is_(None),
            )
            .order_by(AgentTask.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_awaiting_approval(self, user_id: UUID) -> list[AgentTask]:
        stmt = (
            select(AgentTask)
            .where(
                AgentTask.user_id == user_id,
                AgentTask.status == TaskStatus.AWAITING_APPROVAL,
                AgentTask.deleted_at.is_(None),
            )
            .order_by(AgentTask.created_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # AgentExecution
    # ------------------------------------------------------------------

    async def create_execution(
        self, execution: AgentExecution
    ) -> AgentExecution:
        self._session.add(execution)
        await self._session.flush()
        await self._session.refresh(execution)
        return execution

    async def get_execution_by_id(
        self, execution_id: UUID
    ) -> AgentExecution | None:
        return await self._session.get(AgentExecution, execution_id)

    async def list_executions_for_task(
        self, task_id: UUID
    ) -> list[AgentExecution]:
        stmt = (
            select(AgentExecution)
            .where(AgentExecution.task_id == task_id)
            .order_by(AgentExecution.started_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def update_execution(
        self, execution_id: UUID, values: dict
    ) -> None:
        stmt = (
            update(AgentExecution)
            .where(AgentExecution.id == execution_id)
            .values(**values)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def count_executions_by_agent(
        self, task_id: UUID, agent_type: AgentType
    ) -> int:
        stmt = select(func.count()).select_from(AgentExecution).where(
            AgentExecution.task_id == task_id,
            AgentExecution.agent_type == agent_type,
        )
        return (await self._session.execute(stmt)).scalar() or 0

    async def get_failed_executions(
        self, task_id: UUID
    ) -> list[AgentExecution]:
        from app.core.enums import AgentStatus
        stmt = (
            select(AgentExecution)
            .where(
                AgentExecution.task_id == task_id,
                AgentExecution.status == AgentStatus.FAILED,
            )
            .order_by(AgentExecution.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())