"""IOS Knowledge — Shared Types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EntityLabel(str, Enum):
    """Supported Neo4j node labels."""
    ENTITY = "Entity"
    CONCEPT = "Concept"
    DOCUMENT = "Document"
    PERSON = "Person"
    ORGANIZATION = "Organization"
    TECHNOLOGY = "Technology"
    TASK = "Task"
    SESSION = "Session"


class RelationType(str, Enum):
    """Canonical relationship type identifiers (SCREAMING_SNAKE_CASE)."""
    RELATED_TO = "RELATED_TO"
    MENTIONS = "MENTIONS"
    SUBTYPE_OF = "SUBTYPE_OF"
    PREREQUISITE_OF = "PREREQUISITE_OF"
    REQUIRES_CONCEPT = "REQUIRES_CONCEPT"
    USES_TECHNOLOGY = "USES_TECHNOLOGY"
    WORKS_FOR = "WORKS_FOR"
    AUTHORED_BY = "AUTHORED_BY"
    DISCUSSED = "DISCUSSED"
    REFERENCED = "REFERENCED"
    PART_OF = "PART_OF"
    DERIVED_FROM = "DERIVED_FROM"


class TraversalDirection(str, Enum):
    OUT = "OUT"
    IN = "IN"
    BOTH = "BOTH"


class KnowledgeSearchStrategy(str, Enum):
    FULLTEXT = "fulltext"
    GRAPH_TRAVERSAL = "graph_traversal"
    SEMANTIC = "semantic"        # via embedding gateway
    HYBRID = "hybrid"


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class MergeStrategy(str, Enum):
    KEEP_EXISTING = "keep_existing"
    OVERWRITE = "overwrite"
    MERGE_PROPERTIES = "merge_properties"


# ---------------------------------------------------------------------------
# Core graph objects
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeEntity:
    """
    Represents a node in the knowledge graph.

    Maps to a Neo4j node with one primary label and optional extra labels.
    """
    id: str                              # stable identifier (UUID string or slug)
    label: EntityLabel
    name: str
    description: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0             # extraction confidence 0.0–1.0
    source_id: str | None = None        # originating document/task UUID
    source_type: str | None = None      # "document" | "conversation" | "manual"
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    embedding_id: str | None = None     # Qdrant point ID if entity is embedded
    extra_labels: list[str] = field(default_factory=list)

    @property
    def neo4j_labels(self) -> list[str]:
        return [self.label.value] + self.extra_labels


@dataclass
class KnowledgeRelation:
    """
    Represents a directed relationship between two entities.

    Maps to a Neo4j relationship with a canonical type.
    """
    from_id: str
    to_id: str
    relation_type: RelationType
    properties: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    weight: float = 1.0
    source_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass
class KnowledgeTriple:
    """Subject–Predicate–Object knowledge triple (RDF-style)."""
    subject: KnowledgeEntity
    predicate: RelationType
    obj: KnowledgeEntity
    confidence: float = 1.0
    source_id: str | None = None


# ---------------------------------------------------------------------------
# Graph traversal
# ---------------------------------------------------------------------------


@dataclass
class TraversalResult:
    """Result of a graph neighbourhood or path traversal."""
    root_id: str
    nodes: list[KnowledgeEntity] = field(default_factory=list)
    relations: list[KnowledgeRelation] = field(default_factory=list)
    depth_reached: int = 0


@dataclass
class PathResult:
    """Shortest path between two entities."""
    from_id: str
    to_id: str
    path_nodes: list[KnowledgeEntity] = field(default_factory=list)
    path_length: int = 0
    found: bool = False


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeSearchRequest:
    query: str
    strategy: KnowledgeSearchStrategy = KnowledgeSearchStrategy.FULLTEXT
    labels: list[EntityLabel] | None = None
    top_k: int = 10
    min_confidence: float = 0.0
    include_relations: bool = False
    traversal_depth: int = 1


@dataclass
class KnowledgeSearchResult:
    entity: KnowledgeEntity
    score: float
    relations: list[KnowledgeRelation] = field(default_factory=list)
    highlight: str | None = None


@dataclass
class KnowledgeSearchResponse:
    results: list[KnowledgeSearchResult]
    total_found: int
    query: str
    strategy: KnowledgeSearchStrategy


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@dataclass
class ValidationIssue:
    severity: ValidationSeverity
    code: str
    message: str
    field: str | None = None
    entity_id: str | None = None


@dataclass
class ValidationResult:
    is_valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


@dataclass
class MergeResult:
    """Result of merging two entity records."""
    merged_entity: KnowledgeEntity
    source_ids: list[str]
    relations_merged: int = 0
    conflicts: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Index / snapshot
# ---------------------------------------------------------------------------


@dataclass
class IndexStats:
    """Statistics about the current knowledge graph state."""
    total_entities: int = 0
    total_relations: int = 0
    entities_by_label: dict[str, int] = field(default_factory=dict)
    relations_by_type: dict[str, int] = field(default_factory=dict)
    indexed_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass
class GraphSnapshot:
    """Portable snapshot of a subgraph."""
    snapshot_id: str
    entities: list[KnowledgeEntity]
    relations: list[KnowledgeRelation]
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    notes: str | None = None