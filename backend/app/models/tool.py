"""
Intelligence Operating System — Tool Models
===========================================
ORM models for the tool ecosystem domain:

``Tool``           — Registry entry for every available tool, its schema,
                     permissions, and runtime configuration.
``ToolExecution``  — Immutable record of a single tool invocation: inputs,
                     status, timing, sandbox constraints, and error detail.
``ToolResult``     — Structured output of a completed tool execution, stored
                     separately from ``ToolExecution`` to keep the main record
                     lightweight and allow lazy loading of large outputs.

Architecture alignment:
  - ``Tool`` is a configuration entity managed by admins.  It is *not* an
    ORM-modelled implementation of a tool — actual tool logic lives in
    ``intelligence/tools/``.  The registry maps ``ToolName`` enum values to
    runtime-configuration rows.
  - ``ToolExecution`` is append-only.  Failed or timed-out calls create a
    record with ``status = FAILED`` / ``TIMED_OUT``; the record is never
    mutated after the call completes.
  - ``ToolResult`` carries the potentially large output payload (stdout,
    generated files, chart base64, SQL result-sets).  It is stored in JSONB
    to handle heterogeneous output shapes without schema migrations.

Cascade policy:
    ToolExecution → AgentTask         (cascade delete)
    ToolExecution → AgentExecution    (cascade delete)
    ToolResult    → ToolExecution     (cascade delete)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ToolName, ToolStatus
from app.models.base import AuditMixin, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.agent import AgentExecution, AgentTask


# ---------------------------------------------------------------------------
# Tool (Registry)
# ---------------------------------------------------------------------------


class Tool(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    Registry entry for a tool available within IOS.

    Administrators configure tools here; agents reference them by
    ``tool_name`` (the ``ToolName`` enum value).

    Each ``Tool`` record defines:
    - **Identity** — canonical name, display name, description
    - **Schema** — JSON Schema for input validation (``input_schema_json``)
    - **Permissions** — which roles may invoke this tool
    - **Runtime** — execution timeout, sandbox limits, retry policy
    - **Status** — enabled/disabled flag with optional disable reason

    One Tool row per ``ToolName`` value.  Duplicate ``tool_name`` values are
    prevented by the unique constraint.
    """

    __tablename__ = "tools"
    __table_args__ = (
        Index("ix_tools_tool_name", "tool_name", unique=True),
        Index("ix_tools_is_enabled", "is_enabled"),
        {"comment": "Registry of available tools with configuration and permission schema."},
    )

    tool_name: Mapped[ToolName] = mapped_column(
        SAEnum(ToolName, name="tool_name_enum", create_type=True),
        nullable=False,
        unique=True,
        doc="Canonical tool identifier matching the ToolName enum.",
    )
    display_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Human-readable name shown in the UI and logs.",
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="What this tool does and when agents should use it.",
    )
    # Permission configuration
    required_permission: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Permission string that must be in the user's JWT (e.g. 'tools:python:execute').",
    )
    allowed_roles: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
        doc="List of UserRole values that may invoke this tool.",
    )
    # Input/output schema
    input_schema_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        doc="JSON Schema (draft-07) for validating tool input arguments.",
    )
    output_schema_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        doc="JSON Schema describing the tool's output structure.",
    )
    # Runtime constraints
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
        doc="Global enable/disable switch (overrides per-user permissions).",
    )
    disabled_reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Human-readable reason why this tool is disabled.",
    )
    default_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        server_default=text("60"),
        doc="Execution timeout in seconds. Agents may request shorter but not longer.",
    )
    max_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=300,
        server_default=text("300"),
        doc="Absolute maximum timeout; requests above this are clamped.",
    )
    max_retries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=2,
        server_default=text("2"),
        doc="Maximum automatic retry attempts on transient failure.",
    )
    # Sandbox constraints (for python_executor and filesystem)
    sandbox_memory_mb: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Maximum memory (MB) available to sandboxed tool execution.",
    )
    sandbox_cpu_seconds: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Maximum CPU time (seconds) for sandboxed execution.",
    )
    sandbox_allowed_paths: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
        doc="Filesystem paths accessible within the sandbox.",
    )
    sandbox_network_allowed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="Whether sandboxed code may make outbound network calls.",
    )
    # Statistics (denormalised, updated by background job)
    total_executions: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    success_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    failure_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    avg_latency_ms: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Running average latency across all executions.",
    )
    extra_config: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="Tool-specific extra configuration (e.g. SQL connection overrides).",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    executions: Mapped[list["ToolExecution"]] = relationship(
        "ToolExecution",
        back_populates="tool",
        cascade="all, delete-orphan",
        passive_deletes=True,
        doc="All invocation records for this tool.",
    )

    def __repr__(self) -> str:
        return (
            f"<Tool name={self.tool_name} enabled={self.is_enabled} "
            f"executions={self.total_executions}>"
        )


# ---------------------------------------------------------------------------
# ToolExecution
# ---------------------------------------------------------------------------


class ToolExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Immutable record of a single tool invocation.

    Created when an agent dispatches a tool call; updated only once when
    the call completes (success, failure, or timeout).  Never mutated
    after completion.

    Relationships:
    - Belongs to one ``AgentTask`` (for task-level aggregation)
    - Belongs to one ``AgentExecution`` (for step-level tracing)
    - Belongs to one ``Tool`` (for per-tool statistics)
    - Owns one ``ToolResult`` (lazy-loaded output payload)

    The ``tool_call_id`` is a client-generated correlation ID that links
    this execution to the corresponding ``Message`` with role ``TOOL``
    in the conversation history.
    """

    __tablename__ = "tool_executions"
    __table_args__ = (
        Index("ix_tool_executions_task_id", "task_id"),
        Index("ix_tool_executions_agent_execution_id", "agent_execution_id"),
        Index("ix_tool_executions_tool_id", "tool_id"),
        Index("ix_tool_executions_tool_name", "tool_name"),
        Index("ix_tool_executions_status", "status"),
        Index("ix_tool_executions_started_at", "started_at"),
        {"comment": "Immutable per-call records of tool invocations."},
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        nullable=False,
        doc="Parent task.",
    )
    agent_execution_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_executions.id", ondelete="CASCADE"),
        nullable=False,
        doc="The agent execution that dispatched this tool call.",
    )
    tool_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tools.id", ondelete="SET NULL"),
        nullable=True,
        doc="FK to the Tool registry entry (SET NULL if tool is later deleted).",
    )
    tool_name: Mapped[ToolName] = mapped_column(
        SAEnum(ToolName, name="tool_name_enum_te", create_type=False),
        nullable=False,
        doc="Denormalised tool name for queries without join.",
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Client-generated correlation ID linking this call to a Message.tool_call_id.",
    )
    # Lifecycle
    status: Mapped[ToolStatus] = mapped_column(
        SAEnum(ToolStatus, name="tool_status_enum", create_type=True),
        nullable=False,
        default=ToolStatus.PENDING,
        server_default=text("'pending'"),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    # Inputs (serialised arguments passed to the tool)
    input_args: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        doc="Serialised input arguments as validated against Tool.input_schema_json.",
    )
    # Retry tracking
    attempt_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
        doc="Which attempt this is (1 = first, 2 = first retry, etc.).",
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default=text("3"),
    )
    retry_of: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tool_executions.id", ondelete="SET NULL"),
        nullable=True,
        doc="If this is a retry, points to the original failed invocation.",
    )
    # Timeout configuration
    timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        server_default=text("60"),
        doc="Configured timeout for this invocation.",
    )
    timed_out: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    # Sandbox metrics (python_executor / filesystem)
    sandbox_memory_used_mb: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Peak sandbox memory usage in MB.",
    )
    sandbox_cpu_used_seconds: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    # Permission audit
    permission_checked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True once permission was verified before execution.",
    )
    permission_granted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    # Error
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    error_traceback: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    # Security
    sandbox_violation: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True if the execution attempted to violate sandbox constraints.",
    )
    sandbox_violation_detail: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Description of the sandbox violation (for audit).",
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
        back_populates="tool_executions",
    )
    agent_execution: Mapped["AgentExecution"] = relationship(
        "AgentExecution",
        back_populates="tool_executions",
    )
    tool: Mapped["Tool | None"] = relationship(
        "Tool",
        back_populates="executions",
    )
    result: Mapped["ToolResult | None"] = relationship(
        "ToolResult",
        back_populates="execution",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
        doc="Structured output payload (lazy-loaded).",
    )
    original: Mapped["ToolExecution | None"] = relationship(
        "ToolExecution",
        foreign_keys=[retry_of],
        remote_side="ToolExecution.id",
        doc="The execution this is a retry of.",
    )

    def __repr__(self) -> str:
        return (
            f"<ToolExecution id={self.id} tool={self.tool_name} "
            f"status={self.status} attempt={self.attempt_number} "
            f"duration={self.duration_ms}ms>"
        )


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------


class ToolResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Structured output record for a completed ``ToolExecution``.

    Stored separately from ``ToolExecution`` because outputs can be large
    (megabytes of stdout, chart base64, SQL result-sets with thousands of
    rows) and should not be loaded every time a ``ToolExecution`` is
    queried for status or metrics.

    One ``ToolResult`` per ``ToolExecution`` (1:1, created on completion).

    Output fields are tool-type specific:
    - Python executor: ``stdout``, ``stderr``, ``return_value``, ``exit_code``
    - SQL executor:    ``rows_json``, ``column_metadata_json``, ``row_count``
    - Vision/OCR:      ``extracted_text``, ``analysis_json``, ``confidence``
    - Chart generator: ``chart_type``, ``image_base64``, ``storage_path``
    - Report generator: ``storage_path``, ``page_count``, ``format``
    - Filesystem:      ``operation_result_json``

    All tool-specific fields are nullable; only the fields relevant to the
    tool type will be populated.
    """

    __tablename__ = "tool_results"
    __table_args__ = (
        Index("ix_tool_results_execution_id", "execution_id", unique=True),
        Index("ix_tool_results_tool_name", "tool_name"),
        {"comment": "Structured output payloads for completed tool executions."},
    )

    execution_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tool_executions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    tool_name: Mapped[ToolName] = mapped_column(
        SAEnum(ToolName, name="tool_name_enum_tr", create_type=False),
        nullable=False,
        doc="Denormalised tool name for filter queries.",
    )
    # Generic output
    success: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True if the tool completed without error.",
    )
    output_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Brief LLM-generated or heuristic summary of the result for context injection.",
    )
    # --- Python Executor fields ---
    stdout: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Standard output captured from code execution.",
    )
    stderr: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Standard error captured from code execution.",
    )
    return_value: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Serialised Python return value (if the executed function returns a value).",
    )
    exit_code: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Process exit code (0 = success).",
    )
    # --- SQL Executor fields ---
    rows_json: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Query result rows as a list of dicts.",
    )
    column_metadata_json: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Column metadata (name, type, nullable) for the result set.",
    )
    row_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Number of rows returned or affected.",
    )
    query_executed: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="The exact SQL query that was executed (for audit).",
    )
    # --- Vision / OCR fields ---
    extracted_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Text extracted by OCR from the input image.",
    )
    vision_analysis_json: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Structured analysis output from the vision LLM.",
    )
    ocr_confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Aggregate OCR confidence score (0.0–1.0).",
    )
    # --- Chart Generator fields ---
    chart_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
        doc="Type of chart produced: 'bar', 'line', 'scatter', 'heatmap', etc.",
    )
    chart_storage_path: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        doc="Filesystem / object-storage path to the generated chart file.",
    )
    chart_format: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        doc="Output format: 'png', 'svg', 'pdf'.",
    )
    # --- Report Generator fields ---
    report_storage_path: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        doc="Path to the generated report file (PDF or DOCX).",
    )
    report_format: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
        doc="Report format: 'pdf', 'docx'.",
    )
    report_page_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    # --- Filesystem fields ---
    fs_operation_result: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Result of the filesystem operation (files created, bytes written, etc.).",
    )
    # Output size tracking
    output_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        doc="Total size of the tool output in bytes.",
    )
    # Overflow: any fields not covered above
    extra_output: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="Catch-all for tool-specific output fields not covered by named columns.",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    execution: Mapped["ToolExecution"] = relationship(
        "ToolExecution",
        back_populates="result",
    )

    def __repr__(self) -> str:
        return (
            f"<ToolResult id={self.id} tool={self.tool_name} "
            f"success={self.success} size={self.output_size_bytes}B>"
        )