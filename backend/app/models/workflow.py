"""
Intelligence Operating System — Workflow Models
================================================
ORM models for the execution planning and workflow orchestration domain:

``Workflow``      — Stores the structured execution plan (DAG) produced by
                   the ``PlannerAgent`` for a given ``AgentTask``.
``PlannerState``  — Immutable LangGraph state checkpoints enabling:
                    - Crash recovery (resume from last checkpoint)
                    - Human-in-the-loop interrupts (workflow pauses and resumes)
                    - Replay and debugging (step-by-step state inspection)
                    - A/B testing (compare two planner strategies on the same task)

Design notes:
- ``Workflow`` is versioned: if the user rejects a plan and the Planner
  generates a new one, the version counter increments and the old plan is
  preserved (never mutated).
- ``PlannerState`` is append-only: each LangGraph node execution appends a
  new checkpoint row.  The latest checkpoint per ``workflow_id`` is the
  current execution state.
- The ``dag_json`` field in ``Workflow`` stores the full execution DAG:
    {
      "nodes": [{"id": "step_0", "agent": "research", "description": "...",
                 "dependencies": []}],
      "edges": [{"from": "step_0", "to": "step_1", "condition": null}],
      "entry_node": "step_0"
    }

Cascade policy:
    Workflow     → AgentTask    (cascade delete)
    PlannerState → Workflow     (cascade delete)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ApprovalStatus, WorkflowType
from app.models.base import AuditMixin, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.agent import AgentTask


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class Workflow(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    Structured execution plan produced by the ``PlannerAgent`` for a task.

    A ``Workflow`` is a versioned DAG of steps.  Each step specifies:
    - Which agent executes it
    - Its inputs (preceding step outputs)
    - Its success criteria
    - Its timeout and retry policy

    Version management:
        When a plan is rejected and the Planner generates a revised plan,
        a new ``Workflow`` record is created with ``version`` incremented.
        The ``is_current`` flag is set to ``True`` on the new record and
        ``False`` on all predecessors.

    Approval gate:
        If ``requires_approval`` is True, the workflow pauses after plan
        generation.  The ``approval_status`` transitions from ``PENDING``
        → ``APPROVED``/``REJECTED`` based on human input.  Only approved
        workflows proceed to execution.
    """

    __tablename__ = "workflows"
    __table_args__ = (
        Index("ix_workflows_task_id", "task_id"),
        Index("ix_workflows_workflow_type", "workflow_type"),
        Index("ix_workflows_is_current", "is_current"),
        Index("ix_workflows_approval_status", "approval_status"),
        UniqueConstraint("task_id", "version", name="uq_workflows_task_version"),
        {"comment": "Versioned execution plan DAGs produced by the Planner Agent."},
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        doc="Plan version number (increments on re-generation).",
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="True for the most recent plan version.",
    )
    workflow_type: Mapped[WorkflowType] = mapped_column(
        SAEnum(WorkflowType, name="workflow_type_enum_wf", create_type=False),
        nullable=False,
        doc="Workflow category used to select the LangGraph graph definition.",
    )
    # DAG definition
    dag_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        doc="Full DAG definition: nodes, edges, entry node, conditional branches.",
    )
    step_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Number of steps in the execution plan.",
    )
    # Execution progress
    current_step_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    completed_step_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    failed_step_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    # Approval gate
    requires_approval: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus, name="approval_status_enum", create_type=True),
        nullable=False,
        default=ApprovalStatus.PENDING,
        server_default=text("'pending'"),
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        doc="User who approved or rejected this plan.",
    )
    approval_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    # Planner metadata
    planner_model: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Model used by the Planner to generate this plan.",
    )
    planner_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Tokens consumed to generate this plan.",
    )
    planner_latency_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    # Resource estimates
    estimated_total_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Planner's token budget estimate for the full workflow.",
    )
    estimated_duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Planner's time estimate for the full workflow.",
    )
    # Timing
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    extra_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    task: Mapped["AgentTask"] = relationship(
        "AgentTask",
        back_populates="workflow",
    )
    planner_states: Mapped[list["PlannerState"]] = relationship(
        "PlannerState",
        back_populates="workflow",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="PlannerState.sequence",
        doc="Ordered sequence of LangGraph state checkpoints for this workflow.",
    )

    def __repr__(self) -> str:
        return (
            f"<Workflow id={self.id} task={self.task_id} v{self.version} "
            f"steps={self.step_count} approval={self.approval_status}>"
        )


# ---------------------------------------------------------------------------
# PlannerState
# ---------------------------------------------------------------------------


class PlannerState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    An immutable LangGraph state checkpoint.

    Appended after every node execution in the LangGraph graph.  The latest
    checkpoint (highest ``sequence``) per ``workflow_id`` is the current
    execution state.

    Enables:
    - **Crash recovery**: on restart, replay from the last checkpoint.
    - **HITL pausing**: checkpoint before an interrupt; resume after approval.
    - **Debugging**: inspect state at any point in the execution history.
    - **Replay**: step through execution state-by-state for analysis.

    State contents (``state_json``):
        A serialised ``WorkflowState`` TypedDict matching the LangGraph
        graph definition in ``intelligence/graph/state.py``.

    ``checkpoint_id`` is the LangGraph-internal checkpoint identifier used
    to resume execution via ``graph.get_state(checkpoint_id)``.
    """

    __tablename__ = "planner_states"
    __table_args__ = (
        Index("ix_planner_states_workflow_id", "workflow_id"),
        Index("ix_planner_states_sequence", "sequence"),
        Index("ix_planner_states_node_name", "node_name"),
        Index("ix_planner_states_is_checkpoint", "is_interrupt_checkpoint"),
        UniqueConstraint(
            "workflow_id", "sequence", name="uq_planner_states_workflow_seq"
        ),
        {"comment": "Immutable LangGraph state checkpoints for crash recovery and replay."},
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Monotonically increasing sequence number within this workflow.",
    )
    # LangGraph metadata
    checkpoint_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="LangGraph checkpoint ID (from the PostgresSaver or MemorySaver).",
    )
    node_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Name of the LangGraph node that produced this checkpoint.",
    )
    # State snapshot
    state_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        doc="Full serialised WorkflowState at this checkpoint.",
    )
    state_size_bytes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Byte size of the serialised state for monitoring.",
    )
    # Interrupt / HITL flag
    is_interrupt_checkpoint: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True if execution paused at this checkpoint for human review.",
    )
    interrupt_reason: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        doc="Why execution paused (e.g. 'plan_approval', 'tool_approval').",
    )
    resumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp when execution resumed from this checkpoint.",
    )
    # Node execution metadata
    node_duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="How long the node took to execute.",
    )
    node_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    parent_checkpoint_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="LangGraph parent checkpoint ID for tree-structured replay.",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    workflow: Mapped["Workflow"] = relationship(
        "Workflow",
        back_populates="planner_states",
    )

    def __repr__(self) -> str:
        return (
            f"<PlannerState id={self.id} workflow={self.workflow_id} "
            f"seq={self.sequence} node={self.node_name!r} "
            f"interrupt={self.is_interrupt_checkpoint}>"
        )