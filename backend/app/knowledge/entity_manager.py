"""IOS Knowledge — Entity Manager."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.knowledge.base import BaseKnowledge
from app.knowledge.exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    KnowledgeValidationError,
)
from app.knowledge.interfaces import IEntityStore, IKnowledgeEmbeddingGateway
from app.knowledge.types import (
    EntityLabel,
    KnowledgeEntity,
    MergeResult,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)


class EntityManager(BaseKnowledge):
    """
    Owns the full lifecycle of knowledge graph entities.

    Communicates only through IEntityStore and (optionally) an embedding
    gateway for similarity-based duplicate detection.  Contains no direct
    Neo4j or repository access — that lives behind IEntityStore.
    """

    MIN_CONFIDENCE = 0.1

    def __init__(
        self,
        store: IEntityStore,
        embedding_gateway: IKnowledgeEmbeddingGateway | None = None,
        *,
        duplicate_name_threshold: float = 1.0,
    ) -> None:
        super().__init__()
        self._store = store
        self._embed = embedding_gateway
        self._dup_threshold = duplicate_name_threshold

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_entity(
        self,
        name: str,
        label: EntityLabel,
        *,
        description: str | None = None,
        properties: dict | None = None,
        tags: list[str] | None = None,
        confidence: float = 1.0,
        source_id: str | None = None,
        source_type: str | None = None,
        allow_duplicate: bool = False,
    ) -> KnowledgeEntity:
        """
        Create a new entity, guarding against exact-name duplicates unless
        explicitly permitted.

        Raises:
            EntityAlreadyExistsError: A same-name/label entity already exists
                                       and allow_duplicate is False.
            ConfidenceTooLowError: confidence below MIN_CONFIDENCE.
        """
        async with self._span("create_entity", label=label.value):
            self._assert_confidence(confidence)

            if not allow_duplicate:
                existing = await self._store.get_by_name(name, label)
                if existing:
                    raise EntityAlreadyExistsError(
                        f"Entity '{name}' ({label.value}) already exists.",
                        details={"existing_id": existing.id},
                    )

            entity = KnowledgeEntity(
                id=str(uuid4()),
                label=label,
                name=name,
                description=description,
                properties=properties or {},
                tags=tags or [],
                confidence=confidence,
                source_id=source_id,
                source_type=source_type,
                version=1,
            )
            validation = self.validate_entity(entity)
            if not validation.is_valid:
                raise KnowledgeValidationError(
                    f"Entity validation failed for '{name}'.",
                    details={"issues": [i.message for i in validation.errors]},
                )

            saved = await self._store.upsert(entity)
            self._log.info(
                "entity_created", entity_id=saved.id, label=label.value, name=name
            )
            return saved

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_entity(self, entity_id: str) -> KnowledgeEntity:
        entity = await self._store.get(entity_id)
        if entity is None:
            raise EntityNotFoundError(f"Entity '{entity_id}' not found.")
        return entity

    async def get_entity_or_none(self, entity_id: str) -> KnowledgeEntity | None:
        return await self._store.get(entity_id)

    async def find_by_name(
        self, name: str, label: EntityLabel
    ) -> KnowledgeEntity | None:
        return await self._store.get_by_name(name, label)

    async def list_by_label(
        self, label: EntityLabel, *, limit: int = 100, offset: int = 0
    ) -> list[KnowledgeEntity]:
        return await self._store.list_by_label(label, limit=limit, offset=offset)

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def update_entity(
        self,
        entity_id: str,
        *,
        description: str | None = None,
        properties: dict | None = None,
        tags: list[str] | None = None,
        confidence: float | None = None,
    ) -> KnowledgeEntity:
        """Update mutable fields on an entity; increments version."""
        async with self._span("update_entity", entity_id=entity_id):
            entity = await self.get_entity(entity_id)
            if description is not None:
                entity.description = description
            if properties is not None:
                entity.properties = {**entity.properties, **properties}
            if tags is not None:
                entity.tags = tags
            if confidence is not None:
                self._assert_confidence(confidence)
                entity.confidence = confidence
            entity.version += 1
            entity.updated_at = datetime.now(tz=timezone.utc)

            saved = await self._store.upsert(entity)
            self._log.info(
                "entity_updated", entity_id=entity_id, version=saved.version
            )
            return saved

    async def update_confidence(
        self, entity_id: str, confidence: float, *, strategy: str = "max"
    ) -> KnowledgeEntity:
        """Merge a new confidence observation into the existing score."""
        entity = await self.get_entity(entity_id)
        merged = self.merge_confidence(entity.confidence, confidence, strategy=strategy)
        return await self.update_entity(entity_id, confidence=merged)

    async def attach_source(
        self, entity_id: str, source_id: str, source_type: str
    ) -> KnowledgeEntity:
        """Record additional source attribution in entity properties."""
        entity = await self.get_entity(entity_id)
        sources = entity.properties.setdefault("additional_sources", [])
        sources.append({"source_id": source_id, "source_type": source_type})
        return await self.update_entity(entity_id, properties=entity.properties)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_entity(self, entity_id: str, *, detach: bool = True) -> bool:
        async with self._span("delete_entity", entity_id=entity_id):
            deleted = await self._store.delete(entity_id, detach=detach)
            if deleted:
                self._log.info("entity_deleted", entity_id=entity_id)
            return deleted

    # ------------------------------------------------------------------
    # Duplicate detection & resolution
    # ------------------------------------------------------------------

    async def resolve_duplicates(
        self, label: EntityLabel, *, limit: int = 500
    ) -> list[MergeResult]:
        """
        Scan entities of a given label for exact-name-normalised duplicates
        and merge them.  Returns the list of merge results performed.

        Semantic-similarity duplicate detection is delegated to
        KnowledgeMerger when an embedding gateway is available.
        """
        async with self._span("resolve_duplicates", label=label.value):
            entities = await self._store.list_by_label(label, limit=limit)
            groups: dict[str, list[KnowledgeEntity]] = {}
            for e in entities:
                key = self.normalise_name(e.name)
                groups.setdefault(key, []).append(e)

            results: list[MergeResult] = []
            for key, group in groups.items():
                if len(group) < 2:
                    continue
                from app.knowledge.knowledge_merger import KnowledgeMerger
                merger = KnowledgeMerger(self._store, None)  # relation store injected by caller if needed
                sorted_group = sorted(group, key=lambda e: e.confidence, reverse=True)
                primary = sorted_group[0]
                for duplicate in sorted_group[1:]:
                    result = await merger.merge_entities(primary.id, duplicate.id)
                    results.append(result)
                    primary = result.merged_entity

            self._log.info(
                "duplicates_resolved", label=label.value, merges=len(results)
            )
            return results

    async def find_similar_by_embedding(
        self, entity: KnowledgeEntity, *, top_k: int = 5, min_score: float = 0.85
    ) -> list[tuple[str, float]]:
        """
        Find semantically similar entities via the embedding gateway.

        Returns:
            List of (entity_id, similarity_score) tuples, empty if no
            embedding gateway is configured.
        """
        if not self._embed:
            return []
        query = f"{entity.name}. {entity.description or ''}"
        return await self._embed.search_similar(query, top_k=top_k, min_score=min_score)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_entity(self, entity: KnowledgeEntity) -> ValidationResult:
        """Structural validation of an entity before persistence."""
        issues: list[ValidationIssue] = []

        if not entity.name or not entity.name.strip():
            issues.append(
                self.build_validation_error(
                    "EMPTY_NAME", "Entity name cannot be empty.", field="name", entity_id=entity.id
                )
            )
        if len(entity.name) > 500:
            issues.append(
                self.build_validation_error(
                    "NAME_TOO_LONG", "Entity name exceeds 500 characters.", field="name", entity_id=entity.id
                )
            )
        if not (0.0 <= entity.confidence <= 1.0):
            issues.append(
                self.build_validation_error(
                    "INVALID_CONFIDENCE",
                    f"Confidence {entity.confidence} outside [0.0, 1.0].",
                    field="confidence",
                    entity_id=entity.id,
                )
            )
        if entity.description and len(entity.description) > 10_000:
            issues.append(
                self.build_validation_warning(
                    "DESCRIPTION_LONG",
                    "Description exceeds 10,000 characters; consider summarising.",
                    field="description",
                    entity_id=entity.id,
                )
            )

        has_errors = any(i.severity == ValidationSeverity.ERROR for i in issues)
        return ValidationResult(is_valid=not has_errors, issues=issues)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def count(self, label: EntityLabel | None = None) -> int:
        return await self._store.count(label)

    async def exists(self, entity_id: str) -> bool:
        return await self._store.exists(entity_id)