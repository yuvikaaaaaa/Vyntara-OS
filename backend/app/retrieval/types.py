"""IOS Retrieval — Shared Types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RetrievalSource(str, Enum):
    """Where a retrieved item originated from."""
    VECTOR = "vector"                 # Qdrant dense/sparse
    GRAPH = "graph"                   # Neo4j knowledge graph
    MEMORY_EPISODIC = "memory_episodic"
    MEMORY_SEMANTIC = "memory_semantic"
    MEMORY_WORKING = "memory_working"


class RetrievalStrategy(str, Enum):
    VECTOR_ONLY = "vector_only"
    GRAPH_ONLY = "graph_only"
    MEMORY_ONLY = "memory_only"
    HYBRID = "hybrid"                 # combines vector + graph + memory


class FusionMethod(str, Enum):
    RECIPROCAL_RANK = "reciprocal_rank"   # RRF
    WEIGHTED_SUM = "weighted_sum"
    MAX_SCORE = "max_score"


class QueryRewriteStrategy(str, Enum):
    NONE = "none"
    EXPANSION = "expansion"           # add synonyms / related terms
    DECOMPOSITION = "decomposition"   # split into sub-queries
    HYDE = "hyde"                     # hypothetical document embedding


# ---------------------------------------------------------------------------
# Core retrieval objects
# ---------------------------------------------------------------------------


@dataclass
class MetadataFilter:
    """Structured metadata filter applied at the retriever level."""
    user_id: UUID | None = None
    tags: list[str] = field(default_factory=list)
    source_types: list[str] = field(default_factory=list)
    date_from: datetime | None = None
    date_to: datetime | None = None
    labels: list[str] = field(default_factory=list)   # knowledge entity labels
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievedItem:
    """
    A single retrieved unit of content, normalised across all sources.

    Whether it came from a vector store, the graph, or a memory layer,
    all retrievers return content as RetrievedItem so downstream
    components (reranker, context builder) work uniformly.
    """
    id: str
    content: str
    source: RetrievalSource
    score: float                       # raw retriever score (source-specific scale)
    confidence: float = 1.0            # normalised confidence 0.0-1.0
    title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    parent_id: str | None = None       # e.g. document id for a chunk
    citation_label: str | None = None


@dataclass
class RerankedItem:
    """A RetrievedItem after cross-source re-ranking."""
    item: RetrievedItem
    rerank_score: float
    original_rank: int
    final_rank: int


# ---------------------------------------------------------------------------
# Retrieval request / response
# ---------------------------------------------------------------------------


@dataclass
class RetrievalRequest:
    query: str
    user_id: UUID
    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID
    sources: list[RetrievalSource] = field(
        default_factory=lambda: [RetrievalSource.VECTOR, RetrievalSource.GRAPH]
    )
    top_k: int = 10
    min_score: float = 0.0
    metadata_filter: MetadataFilter | None = None
    rewrite_strategy: QueryRewriteStrategy = QueryRewriteStrategy.NONE
    max_context_tokens: int | None = None
    use_cache: bool = True


@dataclass
class RetrievalResponse:
    items: list[RerankedItem]
    total_found: int
    query: str
    rewritten_query: str | None
    sources_used: list[RetrievalSource]
    latency_ms: int
    cache_hit: bool = False


# ---------------------------------------------------------------------------
# Query rewriting
# ---------------------------------------------------------------------------


@dataclass
class QueryRewriteResult:
    original_query: str
    rewritten_query: str
    sub_queries: list[str] = field(default_factory=list)
    strategy: QueryRewriteStrategy = QueryRewriteStrategy.NONE
    hypothetical_document: str | None = None   # for HyDE


# ---------------------------------------------------------------------------
# Context building
# ---------------------------------------------------------------------------


@dataclass
class ContextBudget:
    max_tokens: int
    reserve_tokens: int = 0            # reserved for system prompt / response
    per_item_max_tokens: int | None = None

    @property
    def usable_tokens(self) -> int:
        return max(0, self.max_tokens - self.reserve_tokens)


@dataclass
class BuiltContext:
    """Final assembled context string ready for LLM prompt injection."""
    text: str
    included_items: list[RerankedItem]
    excluded_items: list[RerankedItem]
    total_tokens: int
    citations: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


@dataclass
class CacheEntry:
    key: str
    response: RetrievalResponse
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    ttl_seconds: int = 300

    @property
    def is_expired(self) -> bool:
        age = (datetime.now(tz=timezone.utc) - self.created_at).total_seconds()
        return age >= self.ttl_seconds


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


@dataclass
class RetrievalStats:
    total_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    avg_latency_ms: float = 0.0
    requests_by_source: dict[str, int] = field(default_factory=dict)
    requests_by_strategy: dict[str, int] = field(default_factory=dict)