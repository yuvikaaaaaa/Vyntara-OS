"""IOS Knowledge — Public API.

The Knowledge Engine represents, stores, updates, searches, and reasons
over structured knowledge (entities, relationships, concept graphs).

It is NOT responsible for retrieval pipelines, RAG orchestration, or LLM
interaction — those are consumed by future Retrieval / Planning / Agent
modules that build on top of this layer.

Usage::

    from app.knowledge import (
        EntityManager, RelationManager, GraphManager,
        KnowledgeStore, KnowledgeSearch, KnowledgeIndexer,
        KnowledgeValidator, KnowledgeMerger,
    )
    from app.knowledge import KnowledgeEntity, KnowledgeRelation, EntityLabel
"""

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------
from app.knowledge.types import (
    EntityLabel,
    GraphSnapshot,
    IndexStats,
    KnowledgeEntity,
    KnowledgeRelation,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
    KnowledgeSearchStrategy,
    KnowledgeTriple,
    MergeResult,
    MergeStrategy,
    PathResult,
    RelationType,
    TraversalDirection,
    TraversalResult,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
from app.knowledge.exceptions import (
    ConfidenceTooLowError,
    DuplicateEntityError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    GraphConnectionError,
    GraphTraversalError,
    InvalidEntityLabelError,
    InvalidRelationTypeError,
    KnowledgeError,
    KnowledgeIndexError,
    KnowledgeMergeError,
    KnowledgeSearchError,
    KnowledgeValidationError,
    RelationAlreadyExistsError,
    RelationNotFoundError,
)

# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------
from app.knowledge.interfaces import (
    IEntityStore,
    IGraphTraverser,
    IKnowledgeEmbeddingGateway,
    IRelationStore,
)

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
from app.knowledge.base import BaseKnowledge

# ---------------------------------------------------------------------------
# Storage (Neo4j-backed)
# ---------------------------------------------------------------------------
from app.knowledge.knowledge_store import KnowledgeStore

# ---------------------------------------------------------------------------
# Managers
# ---------------------------------------------------------------------------
from app.knowledge.entity_manager import EntityManager
from app.knowledge.relation_manager import RelationManager
from app.knowledge.graph_manager import GraphManager

# ---------------------------------------------------------------------------
# Search, Index, Validation, Merge
# ---------------------------------------------------------------------------
from app.knowledge.knowledge_search import KnowledgeSearch
from app.knowledge.knowledge_indexer import KnowledgeIndexer
from app.knowledge.knowledge_validator import KnowledgeValidator
from app.knowledge.knowledge_merger import KnowledgeMerger

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    # Types
    "EntityLabel",
    "RelationType",
    "TraversalDirection",
    "KnowledgeSearchStrategy",
    "ValidationSeverity",
    "MergeStrategy",
    "KnowledgeEntity",
    "KnowledgeRelation",
    "KnowledgeTriple",
    "TraversalResult",
    "PathResult",
    "KnowledgeSearchRequest",
    "KnowledgeSearchResult",
    "KnowledgeSearchResponse",
    "ValidationIssue",
    "ValidationResult",
    "MergeResult",
    "IndexStats",
    "GraphSnapshot",
    # Exceptions
    "KnowledgeError",
    "EntityNotFoundError",
    "EntityAlreadyExistsError",
    "RelationNotFoundError",
    "RelationAlreadyExistsError",
    "InvalidEntityLabelError",
    "InvalidRelationTypeError",
    "GraphTraversalError",
    "KnowledgeSearchError",
    "KnowledgeValidationError",
    "KnowledgeMergeError",
    "KnowledgeIndexError",
    "GraphConnectionError",
    "DuplicateEntityError",
    "ConfidenceTooLowError",
    # Interfaces
    "IEntityStore",
    "IRelationStore",
    "IGraphTraverser",
    "IKnowledgeEmbeddingGateway",
    # Base
    "BaseKnowledge",
    # Storage
    "KnowledgeStore",
    # Managers
    "EntityManager",
    "RelationManager",
    "GraphManager",
    # Components
    "KnowledgeSearch",
    "KnowledgeIndexer",
    "KnowledgeValidator",
    "KnowledgeMerger",
]