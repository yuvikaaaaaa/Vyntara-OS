"""
Intelligence Operating System — Memory Models
==============================================
ORM models for the four-layer cognitive memory hierarchy:

``WorkingMemory``   — Conversation-scoped, Redis-backed, session-lived context.
                      PostgreSQL holds a metadata snapshot; live data is in Redis.
``EpisodicMemory``  — Task execution experience records (what happened, how).
``SemanticMemory``  — Long-term factual / conceptual memory (what is known).
``MemorySnapshot``  — Point-in-time snapshots used for memory consolidation and
                      replay (bridging working → episodic → semantic).

Architecture alignment with SDD:
┌─────────────────────────┬───────────────┬─────────────────────────────────┐
│ Layer                   │ Live Backend  │ PostgreSQL Role                 │
├─────────────────────────┼───────────────┼─────────────────────────────────┤
│ WorkingMemory           │ Redis Hash    │ Metadata, TTL, token watermarks │
│ EpisodicMemory          │ Qdrant (vec)  │ Full text, outcome, scores      │
│ SemanticMemory          │ Qdrant (vec)  │ Content, importance, access log │
│ MemorySnapshot          │ PostgreSQL    │ Serialised state for replay      │
└─────────────────────────┴───────────────┴─────────────────────────────────┘

Cascade policy:
    WorkingMemory   → Conversation (cascade delete)
    EpisodicMemory  → User         (cascade delete)
    SemanticMemory  → User         (cascade delete)
    MemorySnapshot  → User         (cascade delete)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
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

from app.core.enums import MemoryOutcome, MemorySourceType, MemoryType
from app.models.base import AuditMixin, Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.user import User


# ---------------------------------------------------------------------------
# WorkingMemory
# ---------------------------------------------------------------------------


class WorkingMemory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Metadata record for a conversation's active working memory.

    The live working memory state is a Redis Hash keyed by
    ``ios:<env>:session:working_memory:<conversation_id>``.
    This PostgreSQL record holds:
    - Reference for health checks and diagnostics
    - Token usage watermarks for sliding-window management
    - Snapshot of the last-known memory state (for crash recovery)
    - Conversation summary (used when context window fills)

    One ``WorkingMemory`` record exists per ``Conversation``.
    It is created when the conversation starts and deleted with the conversation.
    """

    __tablename__ = "working_memories"
    __table_args__ = (
        Index("ix_working_memories_conversation_id", "conversation_id", unique=True),
        Index("ix_working_memories_user_id", "user_id"),
        Index("ix_working_memories_last_accessed", "last_accessed_at"),
        {"comment": "Metadata for Redis-backed conversation working memories."},
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        doc="The conversation whose working memory this record tracks.",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    redis_key: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
        doc="Fully-qualified Redis Hash key for this working memory.",
    )
    current_token_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="Current token load in the context window.",
    )
    max_token_budget: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=32768,
        server_default=text("32768"),
        doc="Configured token capacity before compression triggers.",
    )
    compression_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="How many times the context has been compressed / summarised.",
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp of the last read from Redis.",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp after which the Redis key is expected to have expired.",
    )
    # Crash-recovery snapshot
    snapshot_json: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        doc="Last-written working memory state snapshot (for Redis crash recovery).",
    )
    # Conversation summary (generated when context fills)
    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="LLM-generated summary of older context, injected when window shrinks.",
    )
    summary_token_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    conversation: Mapped["Conversation"] = relationship(
        "Conversation",
        back_populates="working_memory",
    )
    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<WorkingMemory id={self.id} conv={self.conversation_id} "
            f"tokens={self.current_token_count}/{self.max_token_budget}>"
        )


# ---------------------------------------------------------------------------
# EpisodicMemory
# ---------------------------------------------------------------------------


class EpisodicMemory(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    Persistent record of a completed agent task execution (experience record).

    The episodic memory layer enables agents to learn from past experiences —
    before executing a new task, the ``PlannerAgent`` retrieves the most
    semantically similar episodes and injects them as few-shot examples.

    Live Qdrant record:
        Collection: ``episodic_memories``
        Vector: 768-dim dense (all-mpnet-base-v2)
        Payload: ``user_id``, ``outcome``, ``quality_score``, ``tools_used``,
                 ``agents_used``, ``created_at``

    ``qdrant_point_id`` is the reference to the Qdrant vector record.
    """

    __tablename__ = "episodic_memories"
    __table_args__ = (
        Index("ix_episodic_memories_user_id", "user_id"),
        Index("ix_episodic_memories_created_at", "created_at"),
        Index("ix_episodic_memories_outcome", "outcome"),
        Index("ix_episodic_memories_quality_score", "quality_score"),
        Index("ix_episodic_memories_task_id", "agent_task_id"),
        {"comment": "Task execution experience records for agent few-shot learning."},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_task_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("agent_tasks.id", ondelete="SET NULL"),
        nullable=True,
        doc="The task that generated this experience record.",
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Task description and outcome
    task_description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Natural-language description of the task that was executed.",
    )
    execution_summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="LLM-generated narrative summary of how the task was executed.",
    )
    outcome: Mapped[MemoryOutcome] = mapped_column(
        SAEnum(MemoryOutcome, name="memory_outcome_enum", create_type=True),
        nullable=False,
        doc="Task outcome: success, partial, or failure.",
    )
    # Quality signals
    quality_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Overall quality score from the Evaluation Agent (0.0–1.0).",
    )
    reflection_score: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        doc="Reflection quality score (0.0–1.0).",
    )
    user_rating: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Explicit user rating (1–5 stars), if provided.",
    )
    # Execution statistics
    tools_used: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
        doc="List of tool names invoked during this task.",
    )
    agents_used: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
        doc="List of agent types participating in this task.",
    )
    total_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Total token consumption across all agents.",
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Wall-clock duration of the task execution in milliseconds.",
    )
    step_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    # Tags and categorisation
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
    )
    # Qdrant vector reference
    qdrant_point_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        unique=True,
        doc="Qdrant point UUID in the episodic_memories collection.",
    )
    is_embedded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
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
    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<EpisodicMemory id={self.id} outcome={self.outcome} "
            f"quality={self.quality_score}>"
        )


# ---------------------------------------------------------------------------
# SemanticMemory
# ---------------------------------------------------------------------------


class SemanticMemory(UUIDPrimaryKeyMixin, AuditMixin, Base):
    """
    Long-term factual and conceptual memory record.

    Semantic memories are distillations of knowledge extracted from:
    - Conversations (key facts stated by the user)
    - Documents (important concepts from ingested knowledge)
    - Experiences (generalisable lessons from episodic memory)

    Retrieval is via semantic similarity search in Qdrant.  PostgreSQL
    stores the full content, importance score, and access statistics
    for memory management (decay, eviction).

    Importance decay: ``importance`` is reduced by a configurable factor
    each ``SEMANTIC_MEMORY_IMPORTANCE_DECAY_DAYS`` until it falls below
    a threshold, after which the memory is archived (soft-deleted).
    """

    __tablename__ = "semantic_memories"
    __table_args__ = (
        Index("ix_semantic_memories_user_id", "user_id"),
        Index("ix_semantic_memories_importance", "importance"),
        Index("ix_semantic_memories_last_accessed", "last_accessed_at"),
        Index("ix_semantic_memories_source_type", "source_type"),
        Index("ix_semantic_memories_expires_at", "expires_at"),
        {"comment": "Long-term semantic memories extracted from interactions."},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        doc="Full text of the semantic memory.",
    )
    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Compressed summary for context injection (shorter than content).",
    )
    importance: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.5,
        server_default=text("0.5"),
        doc="Importance weight (0.0–1.0). Decays over time; used for eviction.",
    )
    access_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
        doc="How many times this memory has been retrieved.",
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    source_type: Mapped[MemorySourceType] = mapped_column(
        SAEnum(MemorySourceType, name="memory_source_type_enum", create_type=True),
        nullable=False,
        doc="Origin of this memory record.",
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        doc="Optional FK to the originating record (conversation, document, episode).",
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
        server_default=text("'{}'"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC expiry for time-sensitive memories. NULL = permanent.",
    )
    # Qdrant reference
    qdrant_point_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        unique=True,
        doc="Qdrant point UUID in the semantic_memories collection.",
    )
    is_embedded: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
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
    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        preview = self.content[:50].replace("\n", " ") if self.content else ""
        return (
            f"<SemanticMemory id={self.id} importance={self.importance:.2f} "
            f"source={self.source_type} content={preview!r}>"
        )


# ---------------------------------------------------------------------------
# MemorySnapshot
# ---------------------------------------------------------------------------


class MemorySnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    Point-in-time serialised snapshot of a user's memory state.

    Used for:
    1. **Consolidation checkpoints** — before and after the memory
       consolidator promotes working → episodic → semantic memories.
    2. **Replay** — debugging, evaluation, and A/B testing of different
       memory management strategies.
    3. **Export / import** — user data portability.

    Snapshots are append-only; never updated in place.
    Old snapshots are pruned by the memory management job after
    ``retention_days`` (configured per deployment).
    """

    __tablename__ = "memory_snapshots"
    __table_args__ = (
        Index("ix_memory_snapshots_user_id", "user_id"),
        Index("ix_memory_snapshots_snapshot_type", "snapshot_type"),
        Index("ix_memory_snapshots_created_at", "created_at"),
        {"comment": "Point-in-time memory state snapshots for consolidation and replay."},
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_type: Mapped[MemoryType] = mapped_column(
        SAEnum(MemoryType, name="memory_type_enum", create_type=True),
        nullable=False,
        doc="Which memory layer this snapshot covers.",
    )
    trigger: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        doc="What triggered the snapshot: 'consolidation', 'export', 'scheduled', etc.",
    )
    # Counts at time of snapshot
    working_memory_tokens: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    episodic_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    semantic_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    # Serialised state
    state_json: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        doc="Full serialised memory state at snapshot time.",
    )
    size_bytes: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        doc="Size of the serialised JSON in bytes.",
    )
    checksum_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        doc="SHA-256 of the state_json bytes for integrity verification.",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        doc="UTC timestamp after which this snapshot may be pruned.",
    )
    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="Human-readable notes about why this snapshot was taken.",
    )

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------
    user: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return (
            f"<MemorySnapshot id={self.id} type={self.snapshot_type} "
            f"trigger={self.trigger!r} created={self.created_at}>"
        )