"""IOS — Workflow Service."""
from __future__ import annotations

from uuid import UUID

from app.core.enums import ApprovalStatus, WorkflowType
from app.core.exceptions import AuthorizationError, NotFoundError
from app.models.workflow import PlannerState, Workflow
from app.schemas.workflow import WorkflowApproval
from app.services.base import BaseService


class WorkflowService(BaseService):
    """Manages versioned execution plans and LangGraph state checkpoints."""

    async def create_workflow(
        self,
        task_id: UUID,
        workflow_type: WorkflowType,
        dag_json: dict,
        *,
        planner_model: str | None = None,
        planner_tokens: int | None = None,
        planner_latency_ms: int | None = None,
        estimated_total_tokens: int | None = None,
        estimated_duration_ms: int | None = None,
        requires_approval: bool = False,
    ) -> Workflow:
        """Create a new versioned workflow plan for a task."""
        async with self._span("create_workflow", task_id=str(task_id)):
            async with self._transaction() as uow:
                # Supersede any prior current version
                await uow.workflows.supersede_current(task_id)
                version = await uow.workflows.next_version(task_id)

                step_count = len(dag_json.get("nodes", []))
                approval_status = (
                    ApprovalStatus.PENDING if requires_approval else ApprovalStatus.APPROVED
                )
                wf = Workflow(
                    task_id=task_id,
                    version=version,
                    is_current=True,
                    workflow_type=workflow_type,
                    dag_json=dag_json,
                    step_count=step_count,
                    requires_approval=requires_approval,
                    approval_status=approval_status,
                    planner_model=planner_model,
                    planner_tokens=planner_tokens,
                    planner_latency_ms=planner_latency_ms,
                    estimated_total_tokens=estimated_total_tokens,
                    estimated_duration_ms=estimated_duration_ms,
                )
                await uow.workflows.create(wf)
                self._log.info(
                    "workflow_created",
                    workflow_id=str(wf.id),
                    task_id=str(task_id),
                    version=version,
                    steps=step_count,
                )
                return wf

    async def get_current_workflow(self, task_id: UUID) -> Workflow | None:
        async with self._transaction() as uow:
            return await uow.workflows.get_current_workflow(task_id)

    async def get_workflow(self, workflow_id: UUID) -> Workflow:
        async with self._transaction() as uow:
            wf = await uow.workflows.get_by_id(workflow_id)
            if not wf or wf.is_deleted:
                raise NotFoundError("Workflow not found.")
            return wf

    async def list_task_workflows(self, task_id: UUID) -> list[Workflow]:
        async with self._transaction() as uow:
            return await uow.workflows.list_for_task(task_id)

    async def process_approval(
        self,
        workflow_id: UUID,
        approver_id: UUID,
        decision: WorkflowApproval,
    ) -> Workflow:
        """Apply a human approval or rejection to a pending workflow."""
        async with self._span("process_approval", workflow_id=str(workflow_id)):
            async with self._transaction() as uow:
                wf = await uow.workflows.get_by_id(workflow_id)
                if not wf or wf.is_deleted:
                    raise NotFoundError("Workflow not found.")
                if wf.approval_status != ApprovalStatus.PENDING:
                    raise AuthorizationError(
                        f"Workflow is already {wf.approval_status}; cannot change approval."
                    )
                if decision.approved:
                    await uow.workflows.approve(wf, approver_id, decision.notes)
                    self._log.info(
                        "workflow_approved",
                        workflow_id=str(workflow_id),
                        approver=str(approver_id),
                    )
                else:
                    await uow.workflows.reject(wf, approver_id, decision.notes)
                    self._log.info(
                        "workflow_rejected",
                        workflow_id=str(workflow_id),
                        approver=str(approver_id),
                    )
                return wf

    async def record_step_progress(
        self,
        workflow_id: UUID,
        *,
        completed: int = 0,
        failed: int = 0,
        current_index: int | None = None,
    ) -> None:
        async with self._transaction() as uow:
            await uow.workflows.increment_step_counts(
                workflow_id,
                completed=completed,
                failed=failed,
                current_index=current_index,
            )

    # ------------------------------------------------------------------
    # PlannerState checkpoints
    # ------------------------------------------------------------------

    async def append_checkpoint(
        self,
        workflow_id: UUID,
        node_name: str,
        state_json: dict,
        *,
        checkpoint_id: str | None = None,
        parent_checkpoint_id: str | None = None,
        is_interrupt: bool = False,
        interrupt_reason: str | None = None,
        node_duration_ms: int | None = None,
        node_tokens: int | None = None,
    ) -> PlannerState:
        """Append an immutable LangGraph state checkpoint."""
        async with self._span("append_checkpoint"):
            async with self._transaction() as uow:
                seq = await uow.workflows.next_sequence(workflow_id)
                raw = __import__("json").dumps(state_json, default=str)
                cp = PlannerState(
                    workflow_id=workflow_id,
                    sequence=seq,
                    checkpoint_id=checkpoint_id,
                    node_name=node_name,
                    state_json=state_json,
                    state_size_bytes=len(raw.encode()),
                    is_interrupt_checkpoint=is_interrupt,
                    interrupt_reason=interrupt_reason,
                    node_duration_ms=node_duration_ms,
                    node_tokens=node_tokens,
                    parent_checkpoint_id=parent_checkpoint_id,
                )
                return await uow.workflows.append_checkpoint(cp)

    async def get_latest_checkpoint(
        self, workflow_id: UUID
    ) -> PlannerState | None:
        async with self._transaction() as uow:
            return await uow.workflows.get_latest_checkpoint(workflow_id)

    async def list_checkpoints(
        self, workflow_id: UUID
    ) -> list[PlannerState]:
        async with self._transaction() as uow:
            return await uow.workflows.list_checkpoints(workflow_id)

    async def get_interrupt_checkpoints(
        self, workflow_id: UUID
    ) -> list[PlannerState]:
        async with self._transaction() as uow:
            return await uow.workflows.get_interrupt_checkpoints(workflow_id)

    async def resume_checkpoint(
        self, workflow_id: UUID, checkpoint_id: str
    ) -> PlannerState:
        """Mark a HITL interrupt checkpoint as resumed."""
        from datetime import datetime, timezone
        async with self._transaction() as uow:
            cp = await uow.workflows.get_checkpoint_by_id(checkpoint_id, workflow_id)
            if not cp:
                raise NotFoundError("Checkpoint not found.")
            cp.resumed_at = datetime.now(tz=timezone.utc)
            await uow.workflows.flush()
            return cp