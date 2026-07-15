"""IOS Knowledge — Knowledge Merger."""
from __future__ import annotations

from datetime import datetime, timezone

from app.knowledge.base import BaseKnowledge
from app.knowledge.exceptions import (
    EntityNotFoundError,
    KnowledgeMergeError,
)
from app.knowledge.interfaces import IEntityStore, IRelationStore
from app.knowledge.types import (
    KnowledgeEntity,
    MergeResult,
    MergeStrategy,
    TraversalDirection,
)


class KnowledgeMerger(BaseKnowledge):
    """
    Safely merges duplicate knowledge — entities and their relationships —
    while preserving graph consistency.

    Merge semantics:
    - The entity with higher confidence (or explicitly designated primary)
      is retained; the other is retired.
    - Properties are merged per the configured MergeStrategy.
    - Tags are unioned.
    - Confidence is reconciled via BaseKnowledge.merge_confidence().
    - Source attribution from the retired entity is preserved in
      ``properties["additional_sources"]`` on the surviving entity.
    - Version is incremented on the surviving entity to reflect the merge.
    - All relationships touching the retired entity are re-pointed to the
      surviving entity (self-loops resulting from the merge are dropped).
    - The retired entity is deleted (detached) after successful migration.

    A RelationManager instance may be supplied for relation re-pointing;
    if omitted, this class falls back to direct IRelationStore calls.
    """

    def __init__(
        self,
        entity_store: IEntityStore,
        relation_store: IRelationStore | None,
        *,
        default_strategy: MergeStrategy = MergeStrategy.MERGE_PROPERTIES,
    ) -> None:
        super().__init__()
        self._entities = entity_store
        self._relations = relation_store
        self._default_strategy = default_strategy

    # ------------------------------------------------------------------
    # Entity merge
    # ------------------------------------------------------------------

    async def merge_entities(
        self,
        primary_id: str,
        duplicate_id: str,
        *,
        strategy: MergeStrategy | None = None,
    ) -> MergeResult:
        """
        Merge ``duplicate_id`` into ``primary_id``.

        The primary entity survives; the duplicate is retired after its
        relationships are migrated and its metadata reconciled onto the
        primary.

        Args:
            primary_id: Entity to keep.
            duplicate_id: Entity to retire and merge away.
            strategy: Property conflict resolution strategy
                      (defaults to instance default_strategy).

        Returns:
            MergeResult describing the outcome.

        Raises:
            EntityNotFoundError: Either entity does not exist.
            KnowledgeMergeError: Merge cannot proceed (e.g. same id).
        """
        async with self._span(
            "merge_entities", primary=primary_id, duplicate=duplicate_id
        ):
            if primary_id == duplicate_id:
                raise KnowledgeMergeError(
                    "Cannot merge an entity into itself.",
                    details={"entity_id": primary_id},
                )

            primary = await self._entities.get(primary_id)
            duplicate = await self._entities.get(duplicate_id)
            if primary is None:
                raise EntityNotFoundError(f"Primary entity '{primary_id}' not found.")
            if duplicate is None:
                raise EntityNotFoundError(f"Duplicate entity '{duplicate_id}' not found.")

            effective_strategy = strategy or self._default_strategy
            merged_entity = self._merge_entity_fields(primary, duplicate, effective_strategy)

            conflicts = list(
                self.property_diff(primary.properties, duplicate.properties).keys()
            )

            saved = await self._entities.upsert(merged_entity)

            relations_merged = 0
            if self._relations is not None:
                relations_merged = await self._migrate_relations(
                    duplicate_id, primary_id
                )

            await self._entities.delete(duplicate_id, detach=True)

            self._log.info(
                "entities_merged",
                primary=primary_id,
                duplicate=duplicate_id,
                relations_migrated=relations_merged,
                conflicts=len(conflicts),
            )
            return MergeResult(
                merged_entity=saved,
                source_ids=[primary_id, duplicate_id],
                relations_merged=relations_merged,
                conflicts=conflicts,
            )

    async def merge_batch(
        self,
        primary_id: str,
        duplicate_ids: list[str],
        *,
        strategy: MergeStrategy | None = None,
    ) -> MergeResult:
        """
        Merge multiple duplicates into a single primary entity sequentially.

        Args:
            primary_id: Entity to keep.
            duplicate_ids: Entities to retire, merged one at a time.
            strategy: Property conflict resolution strategy.

        Returns:
            Final MergeResult after all duplicates have been merged.
        """
        async with self._span(
            "merge_batch", primary=primary_id, count=str(len(duplicate_ids))
        ):
            total_relations = 0
            all_conflicts: list[str] = []
            all_sources = [primary_id]

            for dup_id in duplicate_ids:
                if dup_id == primary_id:
                    continue
                result = await self.merge_entities(primary_id, dup_id, strategy=strategy)
                total_relations += result.relations_merged
                all_conflicts.extend(result.conflicts)
                all_sources.append(dup_id)

            final_entity = await self._entities.get(primary_id)
            if final_entity is None:
                raise EntityNotFoundError(
                    f"Primary entity '{primary_id}' vanished during batch merge."
                )

            return MergeResult(
                merged_entity=final_entity,
                source_ids=all_sources,
                relations_merged=total_relations,
                conflicts=all_conflicts,
            )

    # ------------------------------------------------------------------
    # Field-level merge logic
    # ------------------------------------------------------------------

    def _merge_entity_fields(
        self,
        primary: KnowledgeEntity,
        duplicate: KnowledgeEntity,
        strategy: MergeStrategy,
    ) -> KnowledgeEntity:
        """
        Compute the merged entity fields without persisting.

        Property merge honours the requested strategy:
        - KEEP_EXISTING: primary properties win on conflict
        - OVERWRITE: duplicate properties win on conflict
        - MERGE_PROPERTIES: union with primary winning ties (default)
        """
        if strategy == MergeStrategy.OVERWRITE:
            merged_properties = {**primary.properties, **duplicate.properties}
        elif strategy == MergeStrategy.KEEP_EXISTING:
            merged_properties = {**duplicate.properties, **primary.properties}
        else:  # MERGE_PROPERTIES
            merged_properties = {**duplicate.properties, **primary.properties}

        # Preserve source attribution history
        additional_sources: list[dict] = list(
            merged_properties.get("additional_sources", [])
        )
        if duplicate.source_id:
            additional_sources.append(
                {"source_id": duplicate.source_id, "source_type": duplicate.source_type}
            )
        if additional_sources:
            merged_properties["additional_sources"] = additional_sources

        merged_tags = sorted(set(primary.tags) | set(duplicate.tags))
        merged_confidence = self.merge_confidence(
            primary.confidence, duplicate.confidence, strategy="max"
        )
        merged_description = primary.description or duplicate.description

        primary.description = merged_description
        primary.properties = merged_properties
        primary.tags = merged_tags
        primary.confidence = merged_confidence
        primary.version += 1
        primary.updated_at = datetime.now(tz=timezone.utc)
        return primary

    # ------------------------------------------------------------------
    # Relation migration
    # ------------------------------------------------------------------

    async def _migrate_relations(self, from_entity_id: str, to_entity_id: str) -> int:
        """
        Re-point all relations touching ``from_entity_id`` to
        ``to_entity_id``, dropping any resulting self-loops, then remove
        the original relations from the retired entity.

        Returns:
            Number of relations successfully migrated.
        """
        relations = await self._relations.get_relations(
            from_entity_id, direction=TraversalDirection.BOTH
        )
        migrated = 0
        for rel in relations:
            new_from = to_entity_id if rel.from_id == from_entity_id else rel.from_id
            new_to = to_entity_id if rel.to_id == from_entity_id else rel.to_id
            if new_from == new_to:
                continue  # drop self-loop created by the merge

            from app.knowledge.types import KnowledgeRelation
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

        await self._relations.delete_all_relations(from_entity_id)
        return migrated

    # ------------------------------------------------------------------
    # Merge candidate suggestion
    # ------------------------------------------------------------------

    async def suggest_merge_candidates(
        self, entity: KnowledgeEntity, candidates: list[KnowledgeEntity]
    ) -> list[tuple[KnowledgeEntity, float]]:
        """
        Score a set of candidate entities by merge suitability against a
        target entity using normalised name similarity as a lightweight
        heuristic (semantic similarity is delegated to EntityManager's
        embedding-gateway integration when available).

        Returns:
            List of (candidate, score) sorted by descending score.
        """
        target_key = self.normalise_name(entity.name)
        scored: list[tuple[KnowledgeEntity, float]] = []
        for c in candidates:
            if c.id == entity.id:
                continue
            if c.label != entity.label:
                continue
            candidate_key = self.normalise_name(c.name)
            score = 1.0 if candidate_key == target_key else self._token_overlap(
                target_key, candidate_key
            )
            if score > 0:
                scored.append((c, score))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored

    @staticmethod
    def _token_overlap(a: str, b: str) -> float:
        """Jaccard token-overlap similarity between two normalised strings."""
        tokens_a, tokens_b = set(a.split()), set(b.split())
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union)