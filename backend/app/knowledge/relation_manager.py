"""IOS Knowledge — Relation Manager."""
from __future__ import annotations

from datetime import datetime, timezone

from app.knowledge.base import BaseKnowledge
from app.knowledge.exceptions import (
    KnowledgeValidationError,
    RelationAlreadyExistsError,
    RelationNotFoundError,
)
from app.knowledge.interfaces import IEntityStore, IRelationStore
from app.knowledge.types import (
    KnowledgeRelation,
    RelationType,
    TraversalDirection,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)


class RelationManager(BaseKnowledge):
    """
    Owns the full lifecycle of knowledge graph relationships.

    Communicates only through IRelationStore for persistence and
    IEntityStore for endpoint existence checks.  No direct Neo4j access.
    """

    MIN_CONFIDENCE = 0.1

    def __init__(
        self,
        relation_store: IRelationStore,
        entity_store: IEntityStore,
    ) -> None:
        super().__init__()
        self._relations = relation_store
        self._entities = entity_store

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: RelationType,
        *,
        properties: dict | None = None,
        confidence: float = 1.0,
        weight: float = 1.0,
        source_id: str | None = None,
        allow_duplicate: bool = True,
    ) -> KnowledgeRelation:
        """
        Create a relationship between two existing entities.

        Raises:
            EntityNotFoundError: Either endpoint does not exist.
            KnowledgeValidationError: Relation fails structural validation.
            RelationAlreadyExistsError: Identical relation exists and
                                         allow_duplicate is False.
        """
        async with self._span(
            "create_relation", type=relation_type.value, from_id=from_id, to_id=to_id
        ):
            self._assert_confidence(confidence)
            await self._assert_endpoints_exist(from_id, to_id)

            relation = KnowledgeRelation(
                from_id=from_id,
                to_id=to_id,
                relation_type=relation_type,
                properties=properties or {},
                confidence=confidence,
                weight=weight,
                source_id=source_id,
            )

            validation = self.validate_relation(relation)
            if not validation.is_valid:
                raise KnowledgeValidationError(
                    f"Relation validation failed: {from_id} -[{relation_type.value}]-> {to_id}",
                    details={"issues": [i.message for i in validation.errors]},
                )

            if not allow_duplicate:
                existing = await self._relations.get_relations(
                    from_id, direction=TraversalDirection.OUT, relation_types=[relation_type]
                )
                if any(r.to_id == to_id for r in existing):
                    raise RelationAlreadyExistsError(
                        f"Relation {from_id} -[{relation_type.value}]-> {to_id} already exists.",
                    )

            await self._relations.upsert(relation)
            self._log.info(
                "relation_created",
                from_id=from_id,
                to_id=to_id,
                type=relation_type.value,
            )
            return relation

    # ------------------------------------------------------------------
    # Read / lookup
    # ------------------------------------------------------------------

    async def get_relations(
        self,
        entity_id: str,
        *,
        direction: TraversalDirection = TraversalDirection.BOTH,
        relation_types: list[RelationType] | None = None,
    ) -> list[KnowledgeRelation]:
        return await self._relations.get_relations(
            entity_id, direction=direction, relation_types=relation_types
        )

    async def relation_exists(
        self, from_id: str, to_id: str, relation_type: RelationType
    ) -> bool:
        outgoing = await self._relations.get_relations(
            from_id, direction=TraversalDirection.OUT, relation_types=[relation_type]
        )
        return any(r.to_id == to_id for r in outgoing)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def update_relation(
        self,
        from_id: str,
        to_id: str,
        relation_type: RelationType,
        *,
        properties: dict | None = None,
        confidence: float | None = None,
        weight: float | None = None,
    ) -> KnowledgeRelation:
        """Update mutable fields on an existing relation (re-upsert)."""
        async with self._span(
            "update_relation", from_id=from_id, to_id=to_id, type=relation_type.value
        ):
            existing_list = await self._relations.get_relations(
                from_id, direction=TraversalDirection.OUT, relation_types=[relation_type]
            )
            existing = next((r for r in existing_list if r.to_id == to_id), None)
            if existing is None:
                raise RelationNotFoundError(
                    f"Relation {from_id} -[{relation_type.value}]-> {to_id} not found."
                )

            if properties is not None:
                existing.properties = {**existing.properties, **properties}
            if confidence is not None:
                self._assert_confidence(confidence)
                existing.confidence = confidence
            if weight is not None:
                existing.weight = weight
            existing.updated_at = datetime.now(tz=timezone.utc)

            await self._relations.upsert(existing)
            return existing

    async def propagate_confidence(
        self, from_id: str, to_id: str, relation_type: RelationType, observed_confidence: float
    ) -> KnowledgeRelation:
        """Merge a new confidence observation into an existing relation."""
        existing_list = await self._relations.get_relations(
            from_id, direction=TraversalDirection.OUT, relation_types=[relation_type]
        )
        existing = next((r for r in existing_list if r.to_id == to_id), None)
        if existing is None:
            raise RelationNotFoundError(
                f"Relation {from_id} -[{relation_type.value}]-> {to_id} not found."
            )
        merged = self.merge_confidence(existing.confidence, observed_confidence)
        return await self.update_relation(from_id, to_id, relation_type, confidence=merged)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_relation(
        self, from_id: str, to_id: str, relation_type: RelationType
    ) -> bool:
        async with self._span(
            "delete_relation", from_id=from_id, to_id=to_id, type=relation_type.value
        ):
            deleted = await self._relations.delete_relation(from_id, to_id, relation_type)
            if deleted:
                self._log.info(
                    "relation_deleted", from_id=from_id, to_id=to_id, type=relation_type.value
                )
            return deleted

    async def delete_all_for_entity(self, entity_id: str) -> int:
        async with self._span("delete_all_for_entity", entity_id=entity_id):
            count = await self._relations.delete_all_relations(entity_id)
            self._log.info("all_relations_deleted", entity_id=entity_id, count=count)
            return count

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    async def merge_relations(
        self, source_entity_id: str, target_entity_id: str
    ) -> int:
        """
        Re-point all relations from source_entity_id to target_entity_id.

        Used when two entities are merged (source is being retired).

        Returns:
            Number of relations re-pointed.
        """
        async with self._span(
            "merge_relations", source=source_entity_id, target=target_entity_id
        ):
            relations = await self._relations.get_relations(
                source_entity_id, direction=TraversalDirection.BOTH
            )
            migrated = 0
            for rel in relations:
                new_from = target_entity_id if rel.from_id == source_entity_id else rel.from_id
                new_to = target_entity_id if rel.to_id == source_entity_id else rel.to_id
                if new_from == new_to:
                    continue  # avoid self-loop after merge
                new_relation = KnowledgeRelation(
                    from_id=new_from,
                    to_id=new_to,
                    relation_type=rel.relation_type,
                    properties=rel.properties,
                    confidence=rel.confidence,
                    weight=rel.weight,
                    source_id=rel.source_id,
                )
                await self._relations.upsert(new_relation)
                migrated += 1

            await self._relations.delete_all_relations(source_entity_id)
            self._log.info(
                "relations_merged",
                source=source_entity_id,
                target=target_entity_id,
                migrated=migrated,
            )
            return migrated

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_relation(self, relation: KnowledgeRelation) -> ValidationResult:
        """Structural validation of a relation before persistence."""
        issues: list[ValidationIssue] = []

        if relation.from_id == relation.to_id:
            issues.append(
                self.build_validation_error(
                    "SELF_LOOP",
                    f"Self-referencing relation not allowed: {relation.from_id}",
                    field="to_id",
                )
            )
        if not (0.0 <= relation.confidence <= 1.0):
            issues.append(
                self.build_validation_error(
                    "INVALID_CONFIDENCE",
                    f"Confidence {relation.confidence} outside [0.0, 1.0].",
                    field="confidence",
                )
            )
        if relation.weight < 0:
            issues.append(
                self.build_validation_error(
                    "NEGATIVE_WEIGHT",
                    f"Relation weight {relation.weight} cannot be negative.",
                    field="weight",
                )
            )
        if not relation.from_id or not relation.to_id:
            issues.append(
                self.build_validation_error(
                    "MISSING_ENDPOINT", "Relation requires both from_id and to_id."
                )
            )

        has_errors = any(i.severity == ValidationSeverity.ERROR for i in issues)
        return ValidationResult(is_valid=not has_errors, issues=issues)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def count(self, relation_type: RelationType | None = None) -> int:
        return await self._relations.count(relation_type)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _assert_endpoints_exist(self, from_id: str, to_id: str) -> None:
        from app.knowledge.exceptions import EntityNotFoundError
        if not await self._entities.exists(from_id):
            raise EntityNotFoundError(f"Source entity '{from_id}' not found.")
        if not await self._entities.exists(to_id):
            raise EntityNotFoundError(f"Target entity '{to_id}' not found.")