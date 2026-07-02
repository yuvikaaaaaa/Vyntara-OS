"""
Intelligence Operating System — Audit Models
=============================================
ORM models for the security audit and event history domains:

``AuditLog``  — Immutable, tamper-evident record of every security-sensitive
                operation: authentication, authorisation decisions, data
                modifications, tool executions, and admin actions.
                Partitioned by month for scalable archival.

``EventLog``  — Structured system event stream: agent lifecycle events,
                memory operations, retrieval events, workflow state
                transitions, and infrastructure events.
                Higher volume than ``AuditLog``; shorter retention policy.

Design principles:
- Both models are **append-only**. The database role used by the application
  has INSERT privilege only on these tables (no UPDATE, no DELETE).
- ``AuditLog`` uses PostgreSQL table partitioning by ``created_at`` month.
  Monthly partition tables are pre-created during startup.
- No foreign-key constraints on ``user_id`` in ``AuditLog`` — audit records
  must survive user deletion. The ``user_id`` is stored as a plain UUID column.
- ``request_id`` enables correlation with structured application logs and OTel
  traces via the shared X-Request-ID header.
- ``checksum`` (SHA-256 of the row's JSON serialisation) enables tamper
  detection in compliance audits.

Retention policy (configured externally, not enforced by this schema):
    AuditLog:  7 years (regulatory compliance)
    EventLog:  90 days (operational monitoring)
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------


class AuditLog(Base):
    """
    Immutable, tamper-evident security audit log.

    Every security-sensitive operation appends a row here.  The application
    database role has INSERT-only privilege on this table.

    Partitioned by ``created_at`` (RANGE partitioning, monthly partitions).
    The primary key is a ``BIGSERIAL`` (not UUID) for maximum insert throughput
    and efficient sequential scan within partitions.

    Categories of events logged:
    - AUTH          : login, logout, token refresh, OAuth callback
    - AUTHZ         : permission checks, role changes, API key operations
    - DATA_READ     : sensitive data reads (memory, documents, user records)
    - DATA_WRITE    : creates, updates, deletes of persistent records
    - TOOL_EXEC     : tool invocations (especially code execution and SQL)
    - AGENT_EXEC    : agent task submissions and completions
    - ADMIN         : user management, configuration changes, system actions
    - SECURITY      : failed auth, rate limit hits, sandbox violations

    Tamper detection:
        The ``checksum`` field holds the SHA-256 of a canonical JSON
        serialisation of the row (excluding ``checksum`` itself).  A
        nightly compliance job recomputes checksums and alerts on mismatches.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_action", "action"),
        Index("ix_audit_logs_category", "category"),
        Index("ix_audit_logs_resource", "resource"),
        Index("ix_audit_logs_outcome", "outcome"),
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_request_id", "request_id"),
        Index("ix_audit_logs_ip_address", "ip_address"),
        {
            "postgresql_partition_by": "RANGE (created_at)",
            "comment": "Immutable security audit log (monthly range partitions).",
        },
    )

    # BIGSERIAL primary key for max insert throughput (no UUID overhead)
    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
        doc="Auto-incrementing integer PK — sequential for efficient partition scans.",
    )
    # Actor — stored as plain UUID (no FK — must survive user deletion)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        doc="UUID of the acting user. NULL for anonymous/system actions.",
    )
    username_snapshot: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Username at time of event — preserved even if user is later renamed/deleted.",
    )
    # Request context
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        doc="X-Request-ID for correlation with application logs and OTel traces.",
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        doc="Session UUID if the action occurred within an authenticated session.",
    )
    ip_address: Mapped[str | None] = mapped_column(
        INET,
        nullable=True,
        doc="Client IP address (INET supports IPv4 and IPv6).",
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    # Event classification
    category: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        doc="Broad event category: AUTH, AUTHZ, DATA_READ, DATA_WRITE, TOOL_EXEC, "
            "AGENT_EXEC, ADMIN, SECURITY.",
    )
    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Specific action code (SCREAMING_SNAKE_CASE), e.g. 'USER_LOGIN', "
            "'API_KEY_CREATED', 'TASK_SUBMITTED', 'TOOL_EXECUTED'.",
    )
    # Resource being acted upon
    resource: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Resource type, e.g. 'user', 'task', 'document', 'api_key'.",
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Resource identifier (UUID string or other ID).",
    )
    # Outcome
    outcome: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        doc="'success', 'failure', 'blocked', 'error'.",
    )
    outcome_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Machine-readable outcome code (matches IosBaseException.code).",
    )
    # Contextual detail
    details: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="Structured context dict — what changed, what was requested, what was denied.",
    )
    before_state: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="State of the resource before a DATA_WRITE operation.",
    )
    after_state: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="State of the resource after a DATA_WRITE operation.",
    )
    # Timing
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Duration of the audited operation in milliseconds.",
    )
    # Tamper detection
    checksum: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="SHA-256 of canonical row serialisation for tamper detection.",
    )
    # Timestamp (no mixin — no updated_at on append-only tables)
    from sqlalchemy import DateTime
    created_at: Mapped["datetime"] = mapped_column(  # type: ignore[name-defined]
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
        doc="UTC timestamp of the event. Used as partition key.",
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog id={self.id} category={self.category} "
            f"action={self.action} outcome={self.outcome} "
            f"user={self.user_id}>"
        )


# Fix the forward reference to datetime in the column definition above
from datetime import datetime as _datetime  # noqa: E402

AuditLog.created_at = mapped_column(  # type: ignore[assignment]
    __import__("sqlalchemy").DateTime(timezone=True),
    nullable=False,
    server_default=text("NOW()"),
)


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------


class EventLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Structured system event stream — higher volume, shorter retention.

    Captures the full lifecycle of IOS components for operational monitoring
    and debugging.  Unlike ``AuditLog``, ``EventLog`` is not security-critical
    and uses UUID PKs with standard timestamp mixins.

    Event categories:
        AGENT      : agent lifecycle (started, completed, failed, retried)
        MEMORY     : memory read / write / consolidation / eviction
        RETRIEVAL  : RAG pipeline events (query, retrieved, reranked)
        WORKFLOW   : plan generated, step started, step completed, HITL gate
        TOOL       : tool call lifecycle
        EMBEDDING  : embedding computation / batch complete
        INFRA      : database pool events, cache hits/misses, health changes
        STREAM     : WebSocket connection / disconnection / error

    These events complement OTel traces (which capture timing and causality)
    with richer structured payloads that may be too large for OTel span attributes.
    """

    __tablename__ = "event_logs"
    __table_args__ = (
        Index("ix_event_logs_category", "category"),
        Index("ix_event_logs_event_type", "event_type"),
        Index("ix_event_logs_task_id", "task_id"),
        Index("ix_event_logs_agent_execution_id", "agent_execution_id"),
        Index("ix_event_logs_session_id", "session_id"),
        Index("ix_event_logs_severity", "severity"),
        Index("ix_event_logs_created_at", "created_at"),
        {"comment": "Structured system event stream for operational monitoring."},
    )

    # Correlation identifiers
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        doc="Task context if this event occurred during task execution (no FK — append-only).",
    )
    agent_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        doc="Agent execution context (no FK — append-only).",
    )
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        doc="Conversation session context.",
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        doc="User context (no FK — append-only).",
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        doc="X-Request-ID for log / trace correlation.",
    )
    # OTel correlation
    trace_id: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        doc="OTel trace ID hex string (32 chars).",
    )
    span_id: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        doc="OTel span ID hex string (16 chars).",
    )
    # Event classification
    category: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        doc="Broad event category: AGENT, MEMORY, RETRIEVAL, WORKFLOW, TOOL, "
            "EMBEDDING, INFRA, STREAM.",
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="Specific event type (SCREAMING_SNAKE_CASE), e.g. 'AGENT_STARTED', "
            "'CHUNK_RETRIEVED', 'MEMORY_CONSOLIDATED'.",
    )
    severity: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="INFO",
        server_default=text("'INFO'"),
        doc="Log severity: DEBUG, INFO, WARNING, ERROR, CRITICAL.",
    )
    # Component identification
    component: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Source component name (e.g. 'PlannerAgent', 'HybridRetriever').",
    )
    component_version: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )
    # Event payload
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Human-readable event description.",
    )
    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="Structured event payload — fields vary by event_type.",
    )
    # Performance metrics embedded in the event
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Duration of the operation that triggered this event.",
    )
    token_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Token count associated with this event (for LLM / embedding events).",
    )
    # Error detail (for ERROR / CRITICAL severity)
    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    stack_trace: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Truncated stack trace for ERROR events (first 5000 chars).",
    )
    is_error: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True for ERROR or CRITICAL severity events (for fast index filtering).",
    )
    # Environment snapshot
    service_instance: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Identifier of the service instance / container that emitted this event.",
    )
    environment: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
        doc="Runtime environment: development, staging, production.",
    )

    def __repr__(self) -> str:
        return (
            f"<EventLog id={self.id} category={self.category} "
            f"type={self.event_type} severity={self.severity} "
            f"component={self.component}>"
        )