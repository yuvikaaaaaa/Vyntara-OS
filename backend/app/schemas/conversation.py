"""IOS — Conversation & Messaging Schemas."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.core.enums import MessageRole, SessionStatus
from app.schemas.base import AuditedSchema, OrmModel, TimestampedSchema, AppModel


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


class ConversationCreate(AppModel):
    title: str | None = Field(default=None, max_length=500)
    tags: list[str] = Field(default_factory=list)
    metadata_: dict = Field(default_factory=dict, alias="metadata")

    model_config = {"populate_by_name": True}


class ConversationUpdate(AppModel):
    title: str | None = Field(default=None, max_length=500)
    status: SessionStatus | None = None
    is_pinned: bool | None = None
    tags: list[str] | None = None


class ConversationRead(AuditedSchema):
    id: UUID
    user_id: UUID
    title: str | None
    status: SessionStatus
    is_pinned: bool
    message_count: int
    total_tokens: int
    summary: str | None
    tags: list[str]
    metadata_: dict = Field(alias="metadata")

    model_config = {"populate_by_name": True, "from_attributes": True}


class ConversationSummary(OrmModel):
    """Lightweight listing item."""

    id: UUID
    title: str | None
    status: SessionStatus
    is_pinned: bool
    message_count: int
    updated_at: datetime


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


class MessageCreate(AppModel):
    role: MessageRole
    content: str = Field(min_length=1)
    content_type: str = "text"
    tool_name: str | None = None
    tool_call_id: str | None = None
    extra_data: dict = Field(default_factory=dict)


class MessageRead(TimestampedSchema):
    id: UUID
    conversation_id: UUID
    agent_task_id: UUID | None
    role: MessageRole
    content: str
    content_type: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    model_id: str | None
    latency_ms: int | None
    confidence_score: float | None
    reflection_score: float | None
    hallucination_flagged: bool
    tool_name: str | None
    tool_call_id: str | None
    extra_data: dict


class MessageSummary(OrmModel):
    id: UUID
    role: MessageRole
    content: str
    created_at: datetime
    total_tokens: int | None
    hallucination_flagged: bool


# ---------------------------------------------------------------------------
# Attachment
# ---------------------------------------------------------------------------


class AttachmentRead(TimestampedSchema):
    id: UUID
    message_id: UUID
    filename: str
    file_type: str
    mime_type: str
    file_size_bytes: int
    storage_path: str
    checksum_sha256: str | None
    is_processed: bool
    knowledge_document_id: UUID | None


class AttachmentUploadResponse(AppModel):
    """Returned immediately after successful file upload."""

    id: UUID
    filename: str
    file_size_bytes: int
    is_processed: bool
    knowledge_document_id: UUID | None = None