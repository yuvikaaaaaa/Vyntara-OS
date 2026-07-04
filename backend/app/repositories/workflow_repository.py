"""IOS — Workflow Repository."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, update

from app.core.enums import ApprovalStatus
from app.models.workflow import PlannerState, Workflow
from app.repositories.base import BaseRepository


class WorkflowRepository(BaseRepository[Workflow]):
    model = Workflow

    async def get_current_workflow(self, task_id: UUID) -> Workflow | None:
        stmt = select(Workflow).where(
            Workflow.task_id == task_id,
            Workflow.is_current.is_(True),
            Workflow.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_for_task(self, task_id: UUID) -> list[Workflow]:
        stmt = (
            select(Workflow)
            .where(
                Workflow.task_id == task_id,
                Workflow.deleted_at.is_(None),
            )
            .order_by(Workflow.version.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def supersede_current(self, task_id: UUID) -> None:
        """Mark all existing workflow versions for a task as not current."""
        stmt = (
            update(Workflow)
            .where(
                Workflow.task_id == task_id,
                Workflow.is_current.is_(True),
            )
            .values(is_current=False)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def next_version(self, task_id: UUID) -> int:
        stmt = select(func.max(Workflow.version)).where(
            Workflow.task_id == task_id
        )
        result = await self._session.execute(stmt)
        current_max = result.scalar() or 0
        return current_max + 1

    async def approve(
        self,
        workflow: Workflow,
        approved_by: UUID,
        notes: str | None,
    ) -> None:
        from datetime import datetime, timezone
        workflow.approval_status = ApprovalStatus.APPROVED
        workflow.approved_by = approved_by
        workflow.approved_at = datetime.now(tz=timezone.utc)
        workflow.approval_notes = notes
        await self._session.flush()

    async def reject(
        self,
        workflow: Workflow,
        rejected_by: UUID,
        notes: str | None,
    ) -> None:
        from datetime import datetime, timezone
        workflow.approval_status = ApprovalStatus.REJECTED
        workflow.approved_by = rejected_by
        workflow.approved_at = datetime.now(tz=timezone.utc)
        workflow.approval_notes = notes
        await self._session.flush()

    async def increment_step_counts(
        self,
        workflow_id: UUID,
        *,
        completed: int = 0,
        failed: int = 0,
        current_index: int | None = None,
    ) -> None:
        values: dict = {}
        if completed:
            values["completed_step_count"] = Workflow.completed_step_count + completed
        if failed:
            values["failed_step_count"] = Workflow.failed_step_count + failed
        if current_index is not None:
            values["current_step_index"] = current_index
        if not values:
            return
        stmt = (
            update(Workflow).where(Workflow.id == workflow_id).values(**values)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    # ------------------------------------------------------------------
    # PlannerState (checkpoints)
    # ------------------------------------------------------------------

    async def append_checkpoint(
        self, checkpoint: PlannerState
    ) -> PlannerState:
        self._session.add(checkpoint)
        await self._session.flush()
        await self._session.refresh(checkpoint)
        return checkpoint

    async def get_latest_checkpoint(
        self, workflow_id: UUID
    ) -> PlannerState | None:
        stmt = (
            select(PlannerState)
            .where(PlannerState.workflow_id == workflow_id)
            .order_by(PlannerState.sequence.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()

    async def list_checkpoints(
        self, workflow_id: UUID
    ) -> list[PlannerState]:
        stmt = (
            select(PlannerState)
            .where(PlannerState.workflow_id == workflow_id)
            .order_by(PlannerState.sequence.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_interrupt_checkpoints(
        self, workflow_id: UUID
    ) -> list[PlannerState]:
        stmt = (
            select(PlannerState)
            .where(
                PlannerState.workflow_id == workflow_id,
                PlannerState.is_interrupt_checkpoint.is_(True),
            )
            .order_by(PlannerState.sequence.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def next_sequence(self, workflow_id: UUID) -> int:
        stmt = select(func.max(PlannerState.sequence)).where(
            PlannerState.workflow_id == workflow_id
        )
        result = await self._session.execute(stmt)
        return (result.scalar() or 0) + 1

    async def get_checkpoint_by_id(
        self, checkpoint_id: str, workflow_id: UUID
    ) -> PlannerState | None:
        stmt = select(PlannerState).where(
            PlannerState.checkpoint_id == checkpoint_id,
            PlannerState.workflow_id == workflow_id,
        )
        result = await self._session.execute(stmt)
        return result.scalars().first()