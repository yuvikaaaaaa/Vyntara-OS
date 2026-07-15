"""IOS Knowledge — Graph Manager."""
from __future__ import annotations

from app.knowledge.base import BaseKnowledge
from app.knowledge.exceptions import GraphTraversalError
from app.knowledge.interfaces import IEntityStore, IGraphTraverser, IRelationStore
from app.knowledge.types import (
    EntityLabel,
    IndexStats,
    KnowledgeEntity,
    PathResult,
    RelationType,
    TraversalDirection,
    TraversalResult,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)


class GraphManager(BaseKnowledge):
    """
    Pure graph orchestration layer.

    Coordinates traversal, path-finding, neighbourhood discovery, integrity
    checks, and statistics computation.  Contains no persistence logic —
    all reads/writes flow through IGraphTraverser, IEntityStore, and
    IRelationStore.
    """

    def __init__(
        self,
        traverser: IGraphTraverser,
        entity_store: IEntityStore,
        relation_store: IRelationStore,
    ) -> None:
        super().__init__()
        self._traverser = traverser
        self._entities = entity_store
        self._relations = relation_store

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    async def traverse(
        self,
        root_id: str,
        *,
        direction: TraversalDirection = TraversalDirection.BOTH,
        relation_types: list[RelationType] | None = None,
        max_depth: int = 2,
        limit: int = 50,
    ) -> TraversalResult:
        """
        Explore the graph neighbourhood around a root entity.

        Raises:
            GraphTraversalError: On traversal failure (missing root, driver error).
        """
        async with self._span("traverse", root_id=root_id, depth=str(max_depth)):
            if not await self._entities.exists(root_id):
                raise GraphTraversalError(
                    f"Cannot traverse: root entity '{root_id}' does not exist.",
                    details={"root_id": root_id},
                )
            try:
                result = await self._traverser.traverse(
                    root_id,
                    direction=direction,
                    relation_types=relation_types,
                    max_depth=max_depth,
                    limit=limit,
                )
                self._log.info(
                    "graph_traversed",
                    root_id=root_id,
                    nodes=len(result.nodes),
                    depth=result.depth_reached,
                )
                return result
            except GraphTraversalError:
                raise
            except Exception as exc:
                raise GraphTraversalError(
                    f"Traversal failed from root '{root_id}': {exc}",
                    details={"root_id": root_id},
                ) from exc

    async def shortest_path(
        self,
        from_id: str,
        to_id: str,
        *,
        max_depth: int = 6,
        relation_types: list[RelationType] | None = None,
    ) -> PathResult:
        """Find the shortest path between two entities."""
        async with self._span("shortest_path", from_id=from_id, to_id=to_id):
            for entity_id in (from_id, to_id):
                if not await self._entities.exists(entity_id):
                    raise GraphTraversalError(
                        f"Cannot find path: entity '{entity_id}' does not exist.",
                        details={"entity_id": entity_id},
                    )
            result = await self._traverser.shortest_path(
                from_id, to_id, max_depth=max_depth, relation_types=relation_types
            )
            self._log.info(
                "shortest_path_computed",
                from_id=from_id,
                to_id=to_id,
                found=result.found,
                length=result.path_length,
            )
            return result

    async def get_neighbours(
        self, entity_id: str, *, depth: int = 1, limit: int = 50
    ) -> list[KnowledgeEntity]:
        """Return direct or multi-hop neighbours of an entity."""
        async with self._span("get_neighbours", entity_id=entity_id):
            return await self._traverser.get_neighbours(entity_id, depth=depth, limit=limit)

    # ------------------------------------------------------------------
    # Integrity verification
    # ------------------------------------------------------------------

    async def verify_integrity(
        self, entity_ids: list[str]
    ) -> ValidationResult:
        """
        Verify graph consistency for a set of entities:
        - All relation endpoints reference existing entities.
        - No orphaned self-loops.

        Args:
            entity_ids: Entities to check (checking the whole graph is
                        expected to be done in batches by the caller).

        Returns:
            ValidationResult aggregating all found issues.
        """
        async with self._span("verify_integrity", count=str(len(entity_ids))):
            issues: list[ValidationIssue] = []

            for entity_id in entity_ids:
                relations = await self._relations.get_relations(
                    entity_id, direction=TraversalDirection.OUT
                )
                for rel in relations:
                    if not await self._entities.exists(rel.to_id):
                        issues.append(
                            self.build_validation_error(
                                "DANGLING_RELATION",
                                f"Relation from '{rel.from_id}' points to "
                                f"non-existent entity '{rel.to_id}'.",
                                entity_id=entity_id,
                            )
                        )
                    if rel.from_id == rel.to_id:
                        issues.append(
                            self.build_validation_warning(
                                "SELF_LOOP",
                                f"Entity '{entity_id}' has a self-referencing relation.",
                                entity_id=entity_id,
                            )
                        )

            has_errors = any(i.severity == ValidationSeverity.ERROR for i in issues)
            result = ValidationResult(is_valid=not has_errors, issues=issues)
            self._log.info(
                "integrity_verified",
                checked=len(entity_ids),
                errors=len(result.errors),
                warnings=len(result.warnings),
            )
            return result

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    async def compute_stats(self) -> IndexStats:
        """Compute aggregate statistics across all entity labels and relation types."""
        async with self._span("compute_stats"):
            entities_by_label: dict[str, int] = {}
            total_entities = 0
            for label in EntityLabel:
                count = await self._entities.count(label)
                entities_by_label[label.value] = count
                total_entities += count

            relations_by_type: dict[str, int] = {}
            total_relations = 0
            for rel_type in RelationType:
                count = await self._relations.count(rel_type)
                relations_by_type[rel_type.value] = count
                total_relations += count

            stats = IndexStats(
                total_entities=total_entities,
                total_relations=total_relations,
                entities_by_label=entities_by_label,
                relations_by_type=relations_by_type,
            )
            self._log.info(
                "graph_stats_computed",
                total_entities=total_entities,
                total_relations=total_relations,
            )
            return stats