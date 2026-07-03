"""IOS — Tool Schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.enums import ToolName, ToolStatus
from app.schemas.base import AppModel, AuditedSchema, OrmModel, TimestampedSchema


# ---------------------------------------------------------------------------
# Tool (Registry)
# ---------------------------------------------------------------------------


class ToolCreate(AppModel):
    """Admin creates a new tool registry entry."""

    tool_name: ToolName
    display_name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1)
    required_permission: str = Field(min_length=1, max_length=100)
    allowed_roles: list[str] = Field(default_factory=list)
    input_schema_json: dict = Field(default_factory=dict)
    output_schema_json: dict = Field(default_factory=dict)
    default_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    max_timeout_seconds: int = Field(default=300, ge=1, le=3600)
    max_retries: int = Field(default=2, ge=0, le=10)
    sandbox_memory_mb: int | None = None
    sandbox_cpu_seconds: float | None = None
    sandbox_network_allowed: bool = False
    extra_config: dict = Field(default_factory=dict)


class ToolUpdate(AppModel):
    display_name: str | None = Field(default=None, max_length=100)
    description: str | None = None
    is_enabled: bool | None = None
    disabled_reason: str | None = None
    default_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    sandbox_memory_mb: int | None = None
    extra_config: dict | None = None


class ToolRead(AuditedSchema):
    id: UUID
    tool_name: ToolName
    display_name: str
    description: str
    required_permission: str
    allowed_roles: list[str]
    input_schema_json: dict
    output_schema_json: dict
    is_enabled: bool
    disabled_reason: str | None
    default_timeout_seconds: int
    max_timeout_seconds: int
    max_retries: int
    sandbox_memory_mb: int | None
    sandbox_cpu_seconds: float | None
    sandbox_network_allowed: bool
    total_executions: int
    success_count: int
    failure_count: int
    avg_latency_ms: float | None


class ToolSummary(OrmModel):
    id: UUID
    tool_name: ToolName
    display_name: str
    is_enabled: bool
    total_executions: int
    avg_latency_ms: float | None


# ---------------------------------------------------------------------------
# Tool invocation request
# ---------------------------------------------------------------------------


class ToolInvokeRequest(AppModel):
    """Direct tool invocation (admin / operator only)."""

    tool_name: ToolName
    input_args: dict = Field(default_factory=dict)
    timeout_seconds: int | None = None


# ---------------------------------------------------------------------------
# ToolExecution
# ---------------------------------------------------------------------------


class ToolExecutionRead(TimestampedSchema):
    id: UUID
    task_id: UUID
    agent_execution_id: UUID
    tool_id: UUID | None
    tool_name: ToolName
    tool_call_id: str | None
    status: ToolStatus
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    input_args: dict
    attempt_number: int
    max_attempts: int
    retry_of: UUID | None
    timeout_seconds: int
    timed_out: bool
    sandbox_memory_used_mb: float | None
    sandbox_cpu_used_seconds: float | None
    permission_checked: bool
    permission_granted: bool
    error_message: str | None
    error_code: str | None
    sandbox_violation: bool


class ToolExecutionSummary(OrmModel):
    id: UUID
    tool_name: ToolName
    status: ToolStatus
    duration_ms: int | None
    attempt_number: int
    sandbox_violation: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------


class ToolResultRead(TimestampedSchema):
    id: UUID
    execution_id: UUID
    tool_name: ToolName
    success: bool
    output_summary: str | None
    # Python executor
    stdout: str | None
    stderr: str | None
    return_value: dict | None
    exit_code: int | None
    # SQL executor
    rows_json: list | None
    column_metadata_json: list | None
    row_count: int | None
    query_executed: str | None
    # Vision / OCR
    extracted_text: str | None
    vision_analysis_json: dict | None
    ocr_confidence: float | None
    # Chart
    chart_type: str | None
    chart_storage_path: str | None
    chart_format: str | None
    # Report
    report_storage_path: str | None
    report_format: str | None
    report_page_count: int | None
    # Filesystem
    fs_operation_result: dict | None
    output_size_bytes: int | None
    extra_output: dict