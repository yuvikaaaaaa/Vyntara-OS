"""
Intelligence Operating System — Conversation Models
====================================================
ORM models for the conversational interface domain:

``Conversation``  — A named session grouping a sequence of messages.
``Message``       — A single turn in a conversation (user / assistant / tool).
``Attachment``    — Files or binary blobs attached to a message.

Schema design decisions:
- Each ``Message`` carries its token count for context-window management.
- ``Attachment`` records point to object storage (``storage_path``); binary
  data is never stored in PostgreSQL.
- Conversation ``status`` controls whether new messages can be appended.
- Full-text indexing on ``Message.content`` is handled via ``pg_trgm`` GIN
  indexes applied after table creation (not inline, to keep Alembic clean).

Foreign-key cascade policy:
    Message   → Conversation  (cascade delete)
    Attachment → Message      (cascade delete)
    Conversation → User       (cascade delete)
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import MessageRole, SessionStatus
from app.models.base import AuditMixin, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.knowledge import Citation
    from app.models.memory import WorkingMemory
    from app.models.user import User
    from app.models.agent import AgentTask


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


class Conversation(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    A named container for a sequence of messages between a user and the system.

    Conversations map directly to the working-memory scope — a single
    ``WorkingMemory`` record is associated with each active conversation.

    Lifecycle:
      ACTIVE  → the user can send new messages
      ARCHIVED → read-only; messages are retained
      DELETED  → soft-deleted; excluded from default queries
    """

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_user_id", "user_id"),
        Index("ix_conversations_status", "status"),
        Index("ix_conversations_created_at", "created_at"),
        Index("ix_conversations_pinned", "is_pinned"),
        {"comment": "Conversation sessions grouping sequences of messages."},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        doc="Owning user.",
    )
    title: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
        doc="Human-readable conversation title (auto-generated or user-set).",
    )
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, name="session_status_enum", create_type=True),
        nullable=False,
        default=SessionStatus.ACTIVE,
        server_default=text("'active'"),
    )
    is_pinned: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="Pinned conversations appear at the top of the UI list.",
    )
    message_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Denormalised message count — updated by trigger or service layer.",
    )
    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Cumulative token consumption across all messages.",
    )
    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="LLM-generated summary of the conversation (created at archival).",
    )
    tags: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
        doc="User-assigned tags for organisation and search.",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    user: Mapped["User"] = relationship(
        "User",
        back_populates="conversations",
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
        doc="All messages in this conversation, ordered by creation time.",
    )
    working_memory: Mapped["WorkingMemory | None"] = relationship(
        "WorkingMemory",
        back_populates="conversation",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
        doc="The single working-memory record associated with this conversation.",
    )
    tasks: Mapped[list["AgentTask"]] = relationship(
        "AgentTask",
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        doc="Agent tasks triggered within this conversation.",
    )

    def __repr__(self) -> str:
        return (
            f"<Conversation id={self.id} title={self.title!r} "
            f"status={self.status} messages={self.message_count}>"
        )


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


class Message(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    A single turn in a ``Conversation``.

    Roles align with LLM API conventions:
      USER       — human-authored input
      ASSISTANT  — IOS-generated response
      SYSTEM     — injected system / context messages
      TOOL       — tool call invocation or result

    Token counts are stored per-message to support:
    - Context-window management (sliding-window truncation)
    - Cost accounting per conversation
    - Evaluation of verbosity

    Messages are never soft-deleted — deletion of a ``Conversation``
    cascades and physically removes them.
    """

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_id", "conversation_id"),
        Index("ix_messages_created_at", "created_at"),
        Index("ix_messages_role", "role"),
        Index("ix_messages_agent_task_id", "agent_task_id"),
        {"comment": "Individual message turns within conversations."},
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_tasks.id", ondelete="SET NULL"),
        nullable=True,
        doc="If this message was generated by an agent task, links to it.",
    )
    role: Mapped[MessageRole] = mapped_column(
        SAEnum(MessageRole, name="message_role_enum", create_type=True),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Plain-text or Markdown message content.",
    )
    content_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="text",
        server_default=text("'text'"),
        doc="Content MIME type: 'text', 'markdown', 'code', 'tool_call', 'tool_result'.",
    )
    # Token accounting
    prompt_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Tokens consumed by the prompt (assistant messages only).",
    )
    completion_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Tokens in the completion (assistant messages only).",
    )
    total_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Total tokens for this turn (prompt + completion).",
    )
    # Model metadata
    model_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Model identifier used to generate this message (e.g. 'llama3.1:8b').",
    )
    latency_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="End-to-end generation latency in milliseconds.",
    )
    # Quality signals
    confidence_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Confidence score computed by the Evaluation Agent (0.0–1.0).",
    )
    reflection_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Reflection quality score from the Reflection Agent (0.0–1.0).",
    )
    hallucination_flagged: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True if the NLI hallucination detector flagged this message.",
    )
    # Structured payload for tool messages
    tool_name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        doc="Tool name for TOOL role messages.",
    )
    tool_call_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        doc="Correlation ID linking a tool call to its result.",
    )
    extra_data: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
        doc="Unstructured extra payload (streaming chunks, raw API response, etc.).",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="messages",
    )
    attachments: Mapped[list["Attachment"]] = relationship(
        "Attachment",
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
        doc="Files and binary blobs attached to this message.",
    )
    citations: Mapped[list["Citation"]] = relationship(
        "Citation",
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
        doc="Source citations supporting claims in this message.",
    )
    task: Mapped["AgentTask | None"] = relationship(
        "AgentTask",
        foreign_keys=[agent_task_id],
        doc="Agent task that produced this message (if any).",
    )

    def __repr__(self) -> str:
        preview = self.content[:40].replace("\n", " ") if self.content else ""
        return (
            f"<Message id={self.id} role={self.role} "
            f"tokens={self.total_tokens} content={preview!r}>"
        )


# ---------------------------------------------------------------------------
# Attachment
# ---------------------------------------------------------------------------


class Attachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    File or binary asset attached to a ``Message``.

    Binary data is stored in the file system (or object storage such as S3).
    Only the metadata and ``storage_path`` reference are persisted here.

    Supported in both user uploads (images, PDFs, CSVs) and system-generated
    outputs (charts, reports).
    """

    __tablename__ = "attachments"
    __table_args__ = (
        Index("ix_attachments_message_id", "message_id"),
        Index("ix_attachments_file_type", "file_type"),
        {"comment": "File attachments associated with conversation messages."},
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        doc="Original filename as provided by the uploader.",
    )
    file_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        doc="File extension without dot: 'pdf', 'png', 'csv', etc.",
    )
    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="MIME type: 'application/pdf', 'image/png', etc.",
    )
    file_size_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        doc="File size in bytes.",
    )
    storage_path: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        doc="Absolute path or object-storage key where the file is stored.",
    )
    checksum_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="SHA-256 hex digest of the file content for integrity verification.",
    )
    is_processed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
        doc="True once the attachment has been ingested into the RAG pipeline.",
    )
    knowledge_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="SET NULL"),
        nullable=True,
        doc="If ingested, links to the resulting KnowledgeDocument.",
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    message: Mapped["Message"] = relationship(
        "Message",
        back_populates="attachments",
    )

    def __repr__(self) -> str:
        return (
            f"<Attachment id={self.id} filename={self.filename!r} "
            f"type={self.file_type} size={self.file_size_bytes}>"
        )