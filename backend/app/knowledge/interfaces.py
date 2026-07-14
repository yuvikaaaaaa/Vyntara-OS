"""IOS Knowledge — Interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.knowledge.types import (
    EntityLabel,
    GraphSnapshot,
    IndexStats,
    KnowledgeEntity,
    KnowledgeRelation,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    PathResult,
    RelationType,
    TraversalDirection,
    TraversalResult,
    ValidationResult,
)


class IEntityStore(ABC):
    """Contract for entity persistence operations."""

    @abstractmethod
    async def upsert(self, entity: KnowledgeEntity) -> KnowledgeEntity:
        """Create or update an entity node. Returns the saved entity."""

    @abstractmethod
    async def get(self, entity_id: str) -> KnowledgeEntity | None:
        """Return entity by id, or None if not found."""

    @abstractmethod
    async def get_by_name(self, name: str, label: EntityLabel) -> KnowledgeEntity | None:
        """Return the first entity matching name + label, or None."""

    @abstractmethod
    async def delete(self, entity_id: str, *, detach: bool = True) -> bool:
        """Delete entity. Returns True if found and deleted."""

    @abstractmethod
    async def list_by_label(
        self,
        label: EntityLabel,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KnowledgeEntity]:
        """Return entities with the given label."""

    @abstractmethod
    async def fulltext_search(
        self, query: str, *, labels: list[EntityLabel] | None = None, limit: int = 10
    ) -> list[KnowledgeEntity]:
        """Full-text search over entity name and description."""

    @abstractmethod
    async def count(self, label: EntityLabel | None = None) -> int:
        """Count entities, optionally filtered by label."""

    @abstractmethod
    async def exists(self, entity_id: str) -> bool:
        """Return True if an entity with the given id exists."""


class IRelationStore(ABC):
    """Contract for relationship persistence operations."""

    @abstractmethod
    async def upsert(self, relation: KnowledgeRelation) -> bool:
        """Create or update a relationship. Returns True on success."""

    @abstractmethod
    async def get_relations(
        self,
        entity_id: str,
        *,
        direction: TraversalDirection = TraversalDirection.BOTH,
        relation_types: list[RelationType] | None = None,
    ) -> list[KnowledgeRelation]:
        """Return relations for an entity, filtered by direction and type."""

    @abstractmethod
    async def delete_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: RelationType,
    ) -> bool:
        """Delete a specific relation. Returns True if found and deleted."""

    @abstractmethod
    async def delete_all_relations(self, entity_id: str) -> int:
        """Delete all relations touching an entity. Returns count deleted."""

    @abstractmethod
    async def count(self, relation_type: RelationType | None = None) -> int:
        """Count relations, optionally filtered by type."""


class IGraphTraverser(ABC):
    """Contract for graph traversal operations."""

    @abstractmethod
    async def traverse(
        self,
        root_id: str,
        *,
        direction: TraversalDirection = TraversalDirection.BOTH,
        relation_types: list[RelationType] | None = None,
        max_depth: int = 2,
        limit: int = 50,
    ) -> TraversalResult:
        """Breadth-first traversal from a root node."""

    @abstractmethod
    async def shortest_path(
        self,
        from_id: str,
        to_id: str,
        *,
        max_depth: int = 6,
        relation_types: list[RelationType] | None = None,
    ) -> PathResult:
        """Find the shortest path between two entities."""

    @abstractmethod
    async def get_neighbours(
        self,
        entity_id: str,
        *,
        depth: int = 1,
        limit: int = 50,
    ) -> list[KnowledgeEntity]:
        """Return direct neighbours of an entity."""


class IKnowledgeEmbeddingGateway(ABC):
    """
    Thin adapter for embedding entity content.

    Decouples the knowledge engine from AI Core specifics.
    """

    @abstractmethod
    async def embed_entity(self, entity: KnowledgeEntity) -> list[float]:
        """Embed entity name + description into a vector."""

    @abstractmethod
    async def search_similar(
        self, query: str, *, top_k: int = 10, min_score: float = 0.0
    ) -> list[tuple[str, float]]:
        """Return (entity_id, score) pairs for semantically similar entities."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding vector dimension."""