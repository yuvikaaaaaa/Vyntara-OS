"""IOS — Workflow & PlannerState Schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.enums import ApprovalStatus, WorkflowType
from app.schemas.base import AppModel, AuditedSchema, OrmModel, TimestampedSchema


# ---------------------------------------------------------------------------
# DAG node / edge value objects (embedded in Workflow.dag_json)
# ---------------------------------------------------------------------------


class DAGNode(AppModel):
    """A single step node in the execution plan DAG."""

    id: str
    agent: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    timeout_seconds: int = 120
    max_retries: int = 3
    requires_approval: bool = False


class DAGEdge(AppModel):
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    condition: str | None = None

    model_config = {"populate_by_name": True}


class ExecutionDAG(AppModel):
    """Full DAG definition stored in Workflow.dag_json."""

    nodes: list[DAGNode]
    edges: list[DAGEdge]
    entry_node: str


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class WorkflowRead(AuditedSchema):
    id: UUID
    task_id: UUID
    version: int
    is_current: bool
    workflow_type: WorkflowType
    dag_json: dict
    step_count: int
    current_step_index: int
    completed_step_count: int
    failed_step_count: int
    requires_approval: bool
    approval_status: ApprovalStatus
    approved_at: datetime | None
    approved_by: UUID | None
    approval_notes: str | None
    planner_model: str | None
    planner_tokens: int | None
    planner_latency_ms: int | None
    estimated_total_tokens: int | None
    estimated_duration_ms: int | None
    started_at: datetime | None
    completed_at: datetime | None


class WorkflowSummary(OrmModel):
    id: UUID
    task_id: UUID
    version: int
    is_current: bool
    workflow_type: WorkflowType
    step_count: int
    approval_status: ApprovalStatus
    created_at: datetime


class WorkflowApproval(AppModel):
    """Operator submits plan approval via API."""

    approved: bool
    notes: str | None = Field(default=None, max_length=2000)


# ---------------------------------------------------------------------------
# PlannerState
# ---------------------------------------------------------------------------


class PlannerStateRead(TimestampedSchema):
    id: UUID
    workflow_id: UUID
    sequence: int
    checkpoint_id: str | None
    node_name: str
    state_size_bytes: int | None
    is_interrupt_checkpoint: bool
    interrupt_reason: str | None
    resumed_at: datetime | None
    node_duration_ms: int | None
    node_tokens: int | None
    parent_checkpoint_id: str | None


class PlannerStateFull(PlannerStateRead):
    """Includes the full serialised state (large — only used in debug endpoints)."""

    state_json: dict