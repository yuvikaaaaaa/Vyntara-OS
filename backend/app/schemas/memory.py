"""IOS — Memory Layer Schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.enums import MemoryOutcome, MemorySourceType, MemoryType
from app.schemas.base import AppModel, AuditedSchema, OrmModel, TimestampedSchema


# ---------------------------------------------------------------------------
# WorkingMemory
# ---------------------------------------------------------------------------


class WorkingMemoryRead(TimestampedSchema):
    id: UUID
    conversation_id: UUID
    user_id: UUID
    redis_key: str
    current_token_count: int
    max_token_budget: int
    compression_count: int
    last_accessed_at: datetime | None
    expires_at: datetime | None
    summary: str | None
    summary_token_count: int


class WorkingMemoryUpdate(AppModel):
    """Used internally by the Memory Agent to update watermarks."""

    current_token_count: int | None = None
    summary: str | None = None
    summary_token_count: int | None = None
    snapshot_json: dict | None = None


# ---------------------------------------------------------------------------
# EpisodicMemory
# ---------------------------------------------------------------------------


class EpisodicMemoryCreate(AppModel):
    """Created by EvaluationAgent / MemoryAgent after task completion."""

    task_description: str = Field(min_length=1)
    execution_summary: str | None = None
    outcome: MemoryOutcome
    quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    reflection_score: float | None = Field(default=None, ge=0.0, le=1.0)
    tools_used: list[str] = Field(default_factory=list)
    agents_used: list[str] = Field(default_factory=list)
    total_tokens: int | None = None
    duration_ms: int | None = None
    step_count: int = 0
    tags: list[str] = Field(default_factory=list)
    extra_data: dict = Field(default_factory=dict)


class EpisodicMemoryRead(AuditedSchema):
    id: UUID
    user_id: UUID
    agent_task_id: UUID | None
    conversation_id: UUID | None
    task_description: str
    execution_summary: str | None
    outcome: MemoryOutcome
    quality_score: float | None
    reflection_score: float | None
    user_rating: int | None
    tools_used: list[str]
    agents_used: list[str]
    total_tokens: int | None
    duration_ms: int | None
    step_count: int
    tags: list[str]
    qdrant_point_id: UUID | None
    is_embedded: bool


class EpisodicMemorySummary(OrmModel):
    id: UUID
    task_description: str
    outcome: MemoryOutcome
    quality_score: float | None
    created_at: datetime


# ---------------------------------------------------------------------------
# SemanticMemory
# ---------------------------------------------------------------------------


class SemanticMemoryCreate(AppModel):
    content: str = Field(min_length=1)
    summary: str | None = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    source_type: MemorySourceType
    source_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    extra_data: dict = Field(default_factory=dict)


class SemanticMemoryUpdate(AppModel):
    summary: str | None = None
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    tags: list[str] | None = None
    expires_at: datetime | None = None


class SemanticMemoryRead(AuditedSchema):
    id: UUID
    user_id: UUID
    content: str
    summary: str | None
    importance: float
    access_count: int
    last_accessed_at: datetime | None
    source_type: MemorySourceType
    source_id: UUID | None
    tags: list[str]
    expires_at: datetime | None
    qdrant_point_id: UUID | None
    is_embedded: bool


class SemanticMemorySummary(OrmModel):
    id: UUID
    summary: str | None
    importance: float
    source_type: MemorySourceType
    tags: list[str]
    created_at: datetime


# ---------------------------------------------------------------------------
# MemorySnapshot
# ---------------------------------------------------------------------------


class MemorySnapshotRead(TimestampedSchema):
    id: UUID
    user_id: UUID
    snapshot_type: MemoryType
    trigger: str
    working_memory_tokens: int | None
    episodic_count: int | None
    semantic_count: int | None
    size_bytes: int | None
    checksum_sha256: str | None
    expires_at: datetime | None
    notes: str | None


# ---------------------------------------------------------------------------
# Cross-layer memory search
# ---------------------------------------------------------------------------


class MemorySearchRequest(AppModel):
    """Search across one or more memory layers by semantic similarity."""

    query: str = Field(min_length=1, max_length=2000)
    layers: list[MemoryType] = Field(
        default_factory=lambda: [
            MemoryType.EPISODIC,
            MemoryType.SEMANTIC,
        ]
    )
    top_k: int = Field(default=5, ge=1, le=50)
    min_score: float = Field(default=0.0, ge=0.0, le=1.0)


class MemorySearchResult(AppModel):
    """Single result from a cross-layer memory search."""

    layer: MemoryType
    score: float
    record_id: UUID
    content_preview: str
    metadata: dict = Field(default_factory=dict)