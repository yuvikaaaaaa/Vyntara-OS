"""IOS — Agent Task & Execution Schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.enums import AgentStatus, AgentType, TaskPriority, TaskStatus, WorkflowType
from app.schemas.base import AppModel, AuditedSchema, OrmModel, TimestampedSchema


# ---------------------------------------------------------------------------
# AgentTask
# ---------------------------------------------------------------------------


class TaskSubmit(AppModel):
    """User-facing task submission payload."""

    description: str = Field(min_length=1, max_length=10_000)
    conversation_id: UUID | None = None
    workflow_type: WorkflowType = WorkflowType.GENERAL
    priority: TaskPriority = TaskPriority.NORMAL
    requires_approval: bool = False
    tags: list[str] = Field(default_factory=list)
    extra_data: dict = Field(default_factory=dict)


class TaskUpdate(AppModel):
    """Internal update payload (status changes, quality rollup)."""

    status: TaskStatus | None = None
    title: str | None = Field(default=None, max_length=500)
    final_output: str | None = None
    total_prompt_tokens: int | None = None
    total_completion_tokens: int | None = None
    total_tokens: int | None = None
    overall_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    hallucination_detected: bool | None = None
    error_message: str | None = None
    error_code: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None


class AgentTaskRead(AuditedSchema):
    id: UUID
    user_id: UUID
    conversation_id: UUID | None
    title: str | None
    description: str
    intent: str | None
    status: TaskStatus
    workflow_type: WorkflowType
    priority: TaskPriority
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    requires_approval: bool
    approval_status: str | None
    overall_quality_score: float | None
    hallucination_detected: bool
    final_output: str | None
    error_message: str | None
    error_code: str | None
    langgraph_thread_id: str | None
    mlflow_run_id: str | None
    tags: list[str]


class AgentTaskSummary(OrmModel):
    id: UUID
    title: str | None
    description: str
    status: TaskStatus
    workflow_type: WorkflowType
    total_tokens: int
    created_at: datetime
    completed_at: datetime | None


class TaskSubmitResponse(AppModel):
    """Returned immediately on task submission."""

    task_id: UUID
    status: TaskStatus
    stream_url: str
    message: str = "Task accepted. Connect to stream_url for real-time updates."


class ApprovalRequest(AppModel):
    """Operator submits approval decision via API."""

    approved: bool
    notes: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# AgentExecution
# ---------------------------------------------------------------------------


class AgentExecutionRead(TimestampedSchema):
    id: UUID
    task_id: UUID
    retry_of: UUID | None
    step_index: int
    step_name: str | None
    agent_type: AgentType
    status: AgentStatus
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    model_id: str | None
    prompt_version: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    output_text: str | None
    reflection_score: float | None
    reflection_critique: str | None
    confidence_score: float | None
    debate_triggered: bool
    retry_count: int
    error_message: str | None
    error_code: str | None
    retrieved_chunk_ids: list[str]
    retrieval_score_avg: float | None


class AgentExecutionSummary(OrmModel):
    id: UUID
    step_index: int
    agent_type: AgentType
    status: AgentStatus
    duration_ms: int | None
    total_tokens: int | None
    reflection_score: float | None