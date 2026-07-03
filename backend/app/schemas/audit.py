"""IOS — Audit & Event Log Schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.base import AppModel, OrmModel


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


class AuditLogRead(AppModel):
    """Read-only schema — AuditLog uses BigInteger PK, not UUID."""

    model_config = {"from_attributes": True}

    id: int
    user_id: UUID | None
    username_snapshot: str | None
    request_id: UUID | None
    session_id: UUID | None
    ip_address: str | None
    user_agent: str | None
    category: str
    action: str
    resource: str
    resource_id: str | None
    outcome: str
    outcome_code: str | None
    details: dict
    before_state: dict | None
    after_state: dict | None
    duration_ms: int | None
    checksum: str | None
    created_at: datetime


class AuditLogFilter(AppModel):
    """Query parameters for audit log search."""

    user_id: UUID | None = None
    category: str | None = None
    action: str | None = None
    resource: str | None = None
    resource_id: str | None = None
    outcome: str | None = None
    ip_address: str | None = None
    from_dt: datetime | None = None
    to_dt: datetime | None = None


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------


class EventLogRead(OrmModel):
    id: UUID
    task_id: UUID | None
    agent_execution_id: UUID | None
    session_id: UUID | None
    user_id: UUID | None
    request_id: UUID | None
    trace_id: str | None
    span_id: str | None
    category: str
    event_type: str
    severity: str
    component: str | None
    component_version: str | None
    message: str
    payload: dict
    duration_ms: int | None
    token_count: int | None
    error_code: str | None
    error_message: str | None
    is_error: bool
    service_instance: str | None
    environment: str | None
    created_at: datetime


class EventLogFilter(AppModel):
    """Query parameters for event log search."""

    task_id: UUID | None = None
    category: str | None = None
    event_type: str | None = None
    severity: str | None = None
    component: str | None = None
    is_error: bool | None = None
    from_dt: datetime | None = None
    to_dt: datetime | None = None


class EventLogCreate(AppModel):
    """Used internally by services to emit structured events."""

    category: str = Field(min_length=1, max_length=30)
    event_type: str = Field(min_length=1, max_length=100)
    severity: str = Field(default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    component: str | None = Field(default=None, max_length=100)
    message: str = Field(min_length=1)
    payload: dict = Field(default_factory=dict)
    task_id: UUID | None = None
    agent_execution_id: UUID | None = None
    session_id: UUID | None = None
    user_id: UUID | None = None
    request_id: UUID | None = None
    trace_id: str | None = Field(default=None, max_length=32)
    span_id: str | None = Field(default=None, max_length=16)
    duration_ms: int | None = None
    token_count: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    stack_trace: str | None = None