"""IOS Memory — Shared Types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class MemoryLayerType(str, Enum):
    WORKING = "working"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    SNAPSHOT = "snapshot"


class MemoryPriority(int, Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


class MemoryOutcome(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"


class SearchStrategy(str, Enum):
    SEMANTIC = "semantic"      # vector similarity only
    RECENCY = "recency"        # chronological order only
    HYBRID = "hybrid"          # score = semantic * weight + recency * (1-weight)
    IMPORTANCE = "importance"  # importance score only


# ---------------------------------------------------------------------------
# Core memory record
# ---------------------------------------------------------------------------


@dataclass
class MemoryRecord:
    """
    Canonical in-memory representation of any memory record.

    Returned by all memory layer read operations.
    Consumed by the ranker, compactor, and search engine.
    """

    id: UUID
    layer: MemoryLayerType
    user_id: UUID
    content: str
    summary: str | None = None
    importance: float = 0.5          # 0.0 – 1.0
    priority: MemoryPriority = MemoryPriority.NORMAL
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_accessed: datetime | None = None
    access_count: int = 0
    expires_at: datetime | None = None
    tags: list[str] = field(default_factory=list)
    qdrant_point_id: UUID | None = None
    source_id: UUID | None = None    # originating task / conversation / document
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now(tz=timezone.utc) >= self.expires_at


@dataclass
class ScoredMemory:
    """A MemoryRecord paired with a composite retrieval score."""

    record: MemoryRecord
    score: float                    # composite score produced by MemoryRanker
    semantic_score: float | None = None
    recency_score: float | None = None
    importance_score: float | None = None


# ---------------------------------------------------------------------------
# Working memory types
# ---------------------------------------------------------------------------


@dataclass
class WorkingMemorySlot:
    """A single key-value slot in working memory."""

    key: str
    value: Any
    token_estimate: int = 0
    priority: MemoryPriority = MemoryPriority.NORMAL
    pinned: bool = False            # pinned slots are never evicted by compactor


@dataclass
class WorkingMemoryState:
    """Complete snapshot of working memory at a point in time."""

    conversation_id: UUID
    user_id: UUID
    slots: dict[str, WorkingMemorySlot] = field(default_factory=dict)
    total_tokens: int = 0
    token_budget: int = 32_768
    compression_count: int = 0
    summary: str | None = None


# ---------------------------------------------------------------------------
# Episodic memory types
# ---------------------------------------------------------------------------


@dataclass
class EpisodicRecord:
    """Rich execution experience record."""

    id: UUID
    user_id: UUID
    task_description: str
    execution_summary: str | None
    outcome: MemoryOutcome
    quality_score: float | None
    tools_used: list[str]
    agents_used: list[str]
    total_tokens: int | None
    duration_ms: int | None
    step_count: int
    tags: list[str]
    created_at: datetime
    qdrant_point_id: UUID | None = None
    agent_task_id: UUID | None = None
    conversation_id: UUID | None = None


# ---------------------------------------------------------------------------
# Search request / response
# ---------------------------------------------------------------------------


@dataclass
class MemorySearchRequest:
    """Cross-layer memory search parameters."""

    query: str
    user_id: UUID
    layers: list[MemoryLayerType] = field(
        default_factory=lambda: [MemoryLayerType.EPISODIC, MemoryLayerType.SEMANTIC]
    )
    strategy: SearchStrategy = SearchStrategy.HYBRID
    top_k: int = 5
    min_score: float = 0.0
    semantic_weight: float = 0.7    # weight of semantic score in hybrid
    tags_filter: list[str] | None = None
    exclude_expired: bool = True


@dataclass
class MemorySearchResult:
    """Single search result with provenance."""

    record: ScoredMemory
    layer: MemoryLayerType
    highlighted_content: str | None = None   # excerpt relevant to the query


@dataclass
class MemorySearchResponse:
    """Full cross-layer search response."""

    results: list[MemorySearchResult]
    total_found: int
    layers_searched: list[MemoryLayerType]
    query: str


# ---------------------------------------------------------------------------
# Compaction types
# ---------------------------------------------------------------------------


@dataclass
class CompactionResult:
    """Result of a memory compaction / compression pass."""

    layer: MemoryLayerType
    records_before: int
    records_after: int
    tokens_before: int
    tokens_after: int
    evicted_ids: list[UUID] = field(default_factory=list)
    promoted_ids: list[UUID] = field(default_factory=list)   # working → episodic
    summary_generated: bool = False


# ---------------------------------------------------------------------------
# Snapshot types
# ---------------------------------------------------------------------------


@dataclass
class SnapshotMeta:
    """Lightweight snapshot descriptor (without full state_json)."""

    id: UUID
    user_id: UUID
    layer: MemoryLayerType
    trigger: str
    created_at: datetime
    size_bytes: int | None
    checksum: str | None
    notes: str | None


@dataclass
class SnapshotRestoreResult:
    """Result returned after restoring a snapshot."""

    snapshot_id: UUID
    layer: MemoryLayerType
    records_restored: int
    success: bool
    error: str | None = None