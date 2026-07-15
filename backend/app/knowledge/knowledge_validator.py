"""IOS Knowledge — Knowledge Validator."""
from __future__ import annotations

from app.knowledge.base import BaseKnowledge
from app.knowledge.interfaces import IEntityStore, IRelationStore
from app.knowledge.types import (
    EntityLabel,
    KnowledgeEntity,
    KnowledgeRelation,
    RelationType,
    TraversalDirection,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)


class KnowledgeValidator(BaseKnowledge):
    """
    Cross-cutting validation for the knowledge graph.

    Complements the structural validation already performed inline by
    EntityManager and RelationManager with graph-level checks that require
    querying multiple records:
    - Duplicate entities (normalised-name collisions within a label)
    - Dangling / invalid relationships
    - Cyclic PREREQUISITE_OF / SUBTYPE_OF chains
    - Missing required metadata fields
    """

    #: Relation types that must never form a cycle
    _ACYCLIC_TYPES: frozenset[RelationType] = frozenset(
        {RelationType.PREREQUISITE_OF, RelationType.SUBTYPE_OF, RelationType.PART_OF}
    )

    def __init__(
        self,
        entity_store: IEntityStore,
        relation_store: IRelationStore,
    ) -> None:
        super().__init__()
        self._entities = entity_store
        self._relations = relation_store

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    async def check_duplicates(
        self, label: EntityLabel, *, limit: int = 1000
    ) -> ValidationResult:
        """
        Detect entities with normalised-identical names within a label.

        Returns:
            ValidationResult with one WARNING issue per duplicate group.
        """
        async with self._span("check_duplicates", label=label.value):
            entities = await self._entities.list_by_label(label, limit=limit)
            groups: dict[str, list[KnowledgeEntity]] = {}
            for e in entities:
                key = self.normalise_name(e.name)
                groups.setdefault(key, []).append(e)

            issues: list[ValidationIssue] = []
            for key, group in groups.items():
                if len(group) > 1:
                    ids = ", ".join(e.id for e in group)
                    issues.append(
                        self.build_validation_warning(
                            "DUPLICATE_ENTITY",
                            f"{len(group)} entities share normalised name "
                            f"'{key}' ({label.value}): [{ids}]",
                        )
                    )
            return ValidationResult(is_valid=True, issues=issues)

    # ------------------------------------------------------------------
    # Relationship validation
    # ------------------------------------------------------------------

    async def check_dangling_relations(
        self, entity_ids: list[str]
    ) -> ValidationResult:
        """
        Detect relations whose endpoints reference non-existent entities.

        Args:
            entity_ids: Entities whose outgoing relations are checked.
        """
        async with self._span("check_dangling_relations", count=str(len(entity_ids))):
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
                                f"Relation '{rel.from_id}' -[{rel.relation_type.value}]-> "
                                f"'{rel.to_id}' points to a non-existent entity.",
                                entity_id=entity_id,
                            )
                        )
            has_errors = any(i.severity == ValidationSeverity.ERROR for i in issues)
            return ValidationResult(is_valid=not has_errors, issues=issues)

    async def check_invalid_relation_direction(
        self, relations: list[KnowledgeRelation]
    ) -> ValidationResult:
        """
        Detect relations violating basic directional semantics
        (e.g. a Person WORKS_FOR a Person instead of an Organization).

        This is a lightweight heuristic check — full semantic type
        constraints belong to a future schema-registry component.
        """
        issues: list[ValidationIssue] = []
        # Directional type expectations: relation_type -> (expected_from_labels, expected_to_labels)
        expectations: dict[RelationType, tuple[set[EntityLabel], set[EntityLabel]]] = {
            RelationType.WORKS_FOR: ({EntityLabel.PERSON}, {EntityLabel.ORGANIZATION}),
            RelationType.AUTHORED_BY: ({EntityLabel.DOCUMENT}, {EntityLabel.PERSON}),
            RelationType.USES_TECHNOLOGY: ({EntityLabel.TASK}, {EntityLabel.TECHNOLOGY}),
        }
        for rel in relations:
            constraint = expectations.get(rel.relation_type)
            if not constraint:
                continue
            from_entity = await self._entities.get(rel.from_id)
            to_entity = await self._entities.get(rel.to_id)
            if not from_entity or not to_entity:
                continue
            expected_from, expected_to = constraint
            if from_entity.label not in expected_from or to_entity.label not in expected_to:
                issues.append(
                    self.build_validation_warning(
                        "UNEXPECTED_RELATION_DIRECTION",
                        f"Relation {rel.relation_type.value} between "
                        f"{from_entity.label.value} and {to_entity.label.value} "
                        f"does not match expected pattern.",
                    )
                )
        return ValidationResult(is_valid=True, issues=issues)

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    async def check_cycles(
        self, root_id: str, relation_type: RelationType, *, max_depth: int = 20
    ) -> ValidationResult:
        """
        Detect cycles in a relation type that must form a DAG
        (e.g. PREREQUISITE_OF, SUBTYPE_OF).

        Uses depth-first search with a visited-path set.

        Raises no exception — cycles are reported as ERROR issues.
        """
        if relation_type not in self._ACYCLIC_TYPES:
            return ValidationResult(is_valid=True)

        async with self._span("check_cycles", root=root_id, type=relation_type.value):
            issues: list[ValidationIssue] = []
            visited_global: set[str] = set()

            async def dfs(node_id: str, path: list[str], depth: int) -> None:
                if depth > max_depth or node_id in visited_global:
                    return
                if node_id in path:
                    cycle_str = " -> ".join(path + [node_id])
                    issues.append(
                        self.build_validation_error(
                            "CYCLE_DETECTED",
                            f"Cycle detected in {relation_type.value}: {cycle_str}",
                            entity_id=node_id,
                        )
                    )
                    return
                relations = await self._relations.get_relations(
                    node_id, direction=TraversalDirection.OUT, relation_types=[relation_type]
                )
                for rel in relations:
                    await dfs(rel.to_id, path + [node_id], depth + 1)
                visited_global.add(node_id)

            await dfs(root_id, [], 0)
            has_errors = any(i.severity == ValidationSeverity.ERROR for i in issues)
            return ValidationResult(is_valid=not has_errors, issues=issues)

    # ------------------------------------------------------------------
    # Metadata validation
    # ------------------------------------------------------------------

    def check_metadata(
        self, entity: KnowledgeEntity, required_fields: list[str]
    ) -> ValidationResult:
        """
        Validate that an entity's properties dict contains all required
        metadata fields (label-specific schema enforcement).

        Args:
            entity: Entity to check.
            required_fields: Property keys that must be present and non-empty.
        """
        issues: list[ValidationIssue] = []
        for field_name in required_fields:
            value = entity.properties.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                issues.append(
                    self.build_validation_error(
                        "MISSING_REQUIRED_METADATA",
                        f"Entity '{entity.id}' ({entity.label.value}) is missing "
                        f"required property '{field_name}'.",
                        field=field_name,
                        entity_id=entity.id,
                    )
                )
        has_errors = any(i.severity == ValidationSeverity.ERROR for i in issues)
        return ValidationResult(is_valid=not has_errors, issues=issues)

    # ------------------------------------------------------------------
    # Composite validation pass
    # ------------------------------------------------------------------

    async def full_validation(
        self, label: EntityLabel, *, sample_size: int = 500
    ) -> ValidationResult:
        """
        Run all applicable graph-level checks for a label and merge results.

        Combines duplicate detection and dangling-relation checks into a
        single report.  Intended for periodic maintenance jobs rather than
        per-write validation.
        """
        async with self._span("full_validation", label=label.value):
            entities = await self._entities.list_by_label(label, limit=sample_size)
            entity_ids = [e.id for e in entities]

            dup_result = await self.check_duplicates(label, limit=sample_size)
            dangling_result = await self.check_dangling_relations(entity_ids)

            all_issues = dup_result.issues + dangling_result.issues
            has_errors = any(i.severity == ValidationSeverity.ERROR for i in all_issues)
            result = ValidationResult(is_valid=not has_errors, issues=all_issues)

            self._log.info(
                "full_validation_complete",
                label=label.value,
                checked=len(entities),
                errors=len(result.errors),
                warnings=len(result.warnings),
            )
            return result