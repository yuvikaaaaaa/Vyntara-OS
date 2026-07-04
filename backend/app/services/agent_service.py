"""IOS — Agent Service."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from app.core.enums import AgentStatus, AgentType, TaskPriority, TaskStatus, WorkflowType
from app.core.exceptions import AuthorizationError, NotFoundError, TaskNotFoundError, WorkflowAbortedError
from app.models.agent import AgentExecution, AgentTask
from app.schemas.agent import ApprovalRequest, TaskSubmit, TaskSubmitResponse, TaskUpdate
from app.services.base import BaseService

try:
    from app.core.exceptions import TaskNotFoundError  # type: ignore[attr-defined]
except ImportError:
    from app.core.exceptions import NotFoundError as TaskNotFoundError  # type: ignore[assignment]


class AgentService(BaseService):
    """Manages agent task lifecycle and execution traces."""

    # ------------------------------------------------------------------
    # Task submission and lifecycle
    # ------------------------------------------------------------------

    async def submit_task(
        self,
        user_id: UUID,
        data: TaskSubmit,
        base_url: str = "",
    ) -> tuple[AgentTask, str]:
        """Create a new task and return it with its streaming URL."""
        async with self._span("submit_task"):
            async with self._transaction() as uow:
                task = AgentTask(
                    user_id=user_id,
                    conversation_id=data.conversation_id,
                    description=data.description,
                    workflow_type=data.workflow_type,
                    priority=data.priority,
                    requires_approval=data.requires_approval,
                    tags=data.tags,
                    extra_data=data.extra_data,
                    status=TaskStatus.PENDING,
                )
                saved = await uow.agents.create(task)
                stream_url = f"{base_url}/api/v1/tasks/{saved.id}/stream"
                self._log.info(
                    "task_submitted",
                    task_id=str(saved.id),
                    user_id=str(user_id),
                    workflow_type=data.workflow_type,
                )
                return saved, stream_url

    async def get_task(self, task_id: UUID, user_id: UUID) -> AgentTask:
        async with self._transaction() as uow:
            task = await uow.agents.get_by_id(task_id)
            if not task or task.is_deleted:
                raise TaskNotFoundError("Task not found.")
            if task.user_id != user_id:
                raise AuthorizationError("Access denied.")
            return task

    async def list_tasks(
        self,
        user_id: UUID,
        *,
        status: TaskStatus | None = None,
        workflow_type: WorkflowType | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[AgentTask], int]:
        async with self._transaction() as uow:
            return await uow.agents.list_for_user(
                user_id,
                status=status,
                workflow_type=workflow_type,
                page=page,
                page_size=page_size,
            )

    async def update_task_status(
        self,
        task_id: UUID,
        status: TaskStatus,
        extra: dict | None = None,
    ) -> None:
        async with self._span("update_status", task_id=str(task_id), status=status):
            async with self._transaction() as uow:
                await uow.agents.update_status(task_id, status, extra=extra or {})
                self._log.info(
                    "task_status_updated",
                    task_id=str(task_id),
                    status=status,
                )

    async def cancel_task(self, task_id: UUID, user_id: UUID) -> None:
        async with self._span("cancel_task", task_id=str(task_id)):
            async with self._transaction() as uow:
                task = await uow.agents.get_by_id(task_id, raise_not_found=True)
                if task.user_id != user_id:
                    raise AuthorizationError("Access denied.")
                terminal = {TaskStatus.COMPLETE, TaskStatus.FAILED, TaskStatus.CANCELLED}
                if task.status in terminal:
                    raise WorkflowAbortedError(
                        f"Task is already {task.status}; cannot cancel."
                    )
                await uow.agents.update_status(task_id, TaskStatus.CANCELLED)
                self._log.info("task_cancelled", task_id=str(task_id))

    async def set_planning(self, task_id: UUID) -> None:
        await self.update_task_status(task_id, TaskStatus.PLANNING)

    async def set_executing(self, task_id: UUID) -> None:
        await self.update_task_status(
            task_id,
            TaskStatus.EXECUTING,
            extra={"started_at": datetime.now(tz=timezone.utc)},
        )

    async def set_complete(
        self,
        task_id: UUID,
        final_output: str,
        *,
        quality_score: float | None = None,
        hallucination_detected: bool = False,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        async with self._transaction() as uow:
            task = await uow.agents.get_by_id(task_id)
            duration_ms: int | None = None
            if task and task.started_at:
                duration_ms = int((now - task.started_at).total_seconds() * 1000)
            await uow.agents.update_status(
                task_id,
                TaskStatus.COMPLETE,
                extra={
                    "final_output": final_output,
                    "completed_at": now,
                    "duration_ms": duration_ms,
                    "overall_quality_score": quality_score,
                    "hallucination_detected": hallucination_detected,
                },
            )

    async def set_failed(
        self, task_id: UUID, error_message: str, error_code: str | None = None
    ) -> None:
        await self.update_task_status(
            task_id,
            TaskStatus.FAILED,
            extra={"error_message": error_message, "error_code": error_code},
        )

    # ------------------------------------------------------------------
    # Token accounting
    # ------------------------------------------------------------------

    async def accumulate_tokens(
        self,
        task_id: UUID,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        async with self._transaction() as uow:
            await uow.agents.accumulate_tokens(
                task_id, prompt_tokens, completion_tokens
            )

    # ------------------------------------------------------------------
    # Human-in-the-loop approval
    # ------------------------------------------------------------------

    async def request_approval(
        self,
        task_id: UUID,
        approval_context: str,
    ) -> None:
        await self.update_task_status(
            task_id,
            TaskStatus.AWAITING_APPROVAL,
            extra={"approval_status": "pending"},
        )
        self._log.info("task_awaiting_approval", task_id=str(task_id))

    async def process_task_approval(
        self,
        task_id: UUID,
        user_id: UUID,
        decision: ApprovalRequest,
    ) -> AgentTask:
        async with self._span("process_task_approval", task_id=str(task_id)):
            async with self._transaction() as uow:
                task = await uow.agents.get_by_id(task_id, raise_not_found=True)
                if task.user_id != user_id:
                    raise AuthorizationError("Access denied.")
                if task.status != TaskStatus.AWAITING_APPROVAL:
                    raise WorkflowAbortedError("Task is not awaiting approval.")
                new_status = TaskStatus.EXECUTING if decision.approved else TaskStatus.CANCELLED
                extra = {
                    "approval_status": "approved" if decision.approved else "rejected",
                    "approval_notes": decision.notes,
                }
                await uow.agents.update_status(task_id, new_status, extra=extra)
                return await uow.agents.get_by_id(task_id)

    async def get_awaiting_approval(self, user_id: UUID) -> list[AgentTask]:
        async with self._transaction() as uow:
            return await uow.agents.get_awaiting_approval(user_id)

    # ------------------------------------------------------------------
    # AgentExecution
    # ------------------------------------------------------------------

    async def create_execution(
        self,
        task_id: UUID,
        step_index: int,
        agent_type: AgentType,
        *,
        step_name: str | None = None,
        model_id: str | None = None,
        prompt_version: str | None = None,
    ) -> AgentExecution:
        async with self._span("create_execution", agent=str(agent_type)):
            execution = AgentExecution(
                task_id=task_id,
                step_index=step_index,
                agent_type=agent_type,
                step_name=step_name,
                status=AgentStatus.IDLE,
                model_id=model_id,
                prompt_version=prompt_version,
                started_at=datetime.now(tz=timezone.utc),
            )
            async with self._transaction() as uow:
                return await uow.agents.create_execution(execution)

    async def update_execution(
        self, execution_id: UUID, values: dict
    ) -> None:
        async with self._transaction() as uow:
            await uow.agents.update_execution(execution_id, values)

    async def complete_execution(
        self,
        execution_id: UUID,
        output_text: str | None,
        *,
        reflection_score: float | None = None,
        reflection_critique: str | None = None,
        confidence_score: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        retrieved_chunk_ids: list[str] | None = None,
    ) -> None:
        now = datetime.now(tz=timezone.utc)
        async with self._transaction() as uow:
            ex = await uow.agents.get_execution_by_id(execution_id)
            duration_ms: int | None = None
            if ex and ex.started_at:
                duration_ms = int((now - ex.started_at).total_seconds() * 1000)
            total = (prompt_tokens or 0) + (completion_tokens or 0) or None
            await uow.agents.update_execution(
                execution_id,
                {
                    "status": AgentStatus.COMPLETE,
                    "completed_at": now,
                    "duration_ms": duration_ms,
                    "output_text": output_text,
                    "reflection_score": reflection_score,
                    "reflection_critique": reflection_critique,
                    "confidence_score": confidence_score,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total,
                    "retrieved_chunk_ids": retrieved_chunk_ids or [],
                },
            )
            if prompt_tokens or completion_tokens:
                await uow.agents.accumulate_tokens(
                    ex.task_id if ex else ...,
                    prompt_tokens or 0,
                    completion_tokens or 0,
                )

    async def fail_execution(
        self,
        execution_id: UUID,
        error_message: str,
        error_code: str | None = None,
        traceback: str | None = None,
    ) -> None:
        async with self._transaction() as uow:
            await uow.agents.update_execution(
                execution_id,
                {
                    "status": AgentStatus.FAILED,
                    "completed_at": datetime.now(tz=timezone.utc),
                    "error_message": error_message,
                    "error_code": error_code,
                    "error_traceback": traceback,
                },
            )

    async def list_executions(self, task_id: UUID) -> list[AgentExecution]:
        async with self._transaction() as uow:
            return await uow.agents.list_executions_for_task(task_id)

    async def set_mlflow_run(self, task_id: UUID, mlflow_run_id: str) -> None:
        async with self._transaction() as uow:
            task = await uow.agents.get_by_id(task_id, raise_not_found=True)
            await uow.agents.update(task, {"mlflow_run_id": mlflow_run_id})