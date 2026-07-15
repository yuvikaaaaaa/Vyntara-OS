"""IOS Knowledge — Knowledge Indexer."""
from __future__ import annotations

from app.knowledge.base import BaseKnowledge
from app.knowledge.exceptions import KnowledgeIndexError
from app.knowledge.graph_manager import GraphManager
from app.knowledge.interfaces import IEntityStore, IKnowledgeEmbeddingGateway
from app.knowledge.types import EntityLabel, IndexStats, KnowledgeEntity


class KnowledgeIndexer(BaseKnowledge):
    """
    Maintains the searchability and freshness of the knowledge graph index.

    Responsibilities:
    - Compute and cache aggregate index statistics (via GraphManager)
    - Backfill embeddings for entities missing an embedding_id
    - Provide hooks for scheduled re-indexing jobs

    Note: Neo4j's native full-text index is maintained automatically by
    the database on write; this component focuses on the vector-embedding
    side of the index, which requires explicit computation.
    """

    def __init__(
        self,
        entity_store: IEntityStore,
        graph_manager: GraphManager,
        embedding_gateway: IKnowledgeEmbeddingGateway | None = None,
        *,
        batch_size: int = 50,
    ) -> None:
        super().__init__()
        self._entities = entity_store
        self._graph = graph_manager
        self._embed = embedding_gateway
        self._batch_size = batch_size
        self._cached_stats: IndexStats | None = None

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    async def refresh_stats(self) -> IndexStats:
        """Recompute and cache index statistics."""
        async with self._span("refresh_stats"):
            stats = await self._graph.compute_stats()
            self._cached_stats = stats
            self._log.info(
                "index_stats_refreshed",
                entities=stats.total_entities,
                relations=stats.total_relations,
            )
            return stats

    def get_cached_stats(self) -> IndexStats | None:
        """Return the last computed stats without recomputation."""
        return self._cached_stats

    # ------------------------------------------------------------------
    # Embedding backfill
    # ------------------------------------------------------------------

    async def backfill_embeddings(
        self, label: EntityLabel | None = None
    ) -> int:
        """
        Embed all entities (optionally scoped to one label) that are
        missing an ``embedding_id``.

        Returns:
            Number of entities embedded.

        Raises:
            KnowledgeIndexError: If no embedding gateway is configured.
        """
        if not self._embed:
            raise KnowledgeIndexError(
                "Cannot backfill embeddings: no embedding gateway configured."
            )

        async with self._span("backfill_embeddings"):
            labels = [label] if label else list(EntityLabel)
            total_embedded = 0

            for lbl in labels:
                offset = 0
                while True:
                    batch = await self._entities.list_by_label(
                        lbl, limit=self._batch_size, offset=offset
                    )
                    if not batch:
                        break
                    unembedded = [e for e in batch if not e.embedding_id]
                    for entity in unembedded:
                        await self._embed_one(entity)
                        total_embedded += 1
                    offset += self._batch_size
                    if len(batch) < self._batch_size:
                        break

            self._log.info("embedding_backfill_complete", total_embedded=total_embedded)
            return total_embedded

    async def reindex_entity(self, entity_id: str) -> bool:
        """
        Force re-embedding of a single entity (e.g. after content update).

        Returns:
            True if the entity was found and re-embedded.
        """
        if not self._embed:
            raise KnowledgeIndexError(
                "Cannot reindex: no embedding gateway configured."
            )
        entity = await self._entities.get(entity_id)
        if entity is None:
            return False
        await self._embed_one(entity)
        return True

    # ------------------------------------------------------------------
    # Health / diagnostics
    # ------------------------------------------------------------------

    async def coverage_report(self) -> dict[str, dict[str, int]]:
        """
        Return embedding coverage per label: {label: {total, embedded}}.

        Useful for monitoring dashboards showing index completeness.
        """
        async with self._span("coverage_report"):
            report: dict[str, dict[str, int]] = {}
            for label in EntityLabel:
                total = await self._entities.count(label)
                if total == 0:
                    report[label.value] = {"total": 0, "embedded": 0}
                    continue
                sample = await self._entities.list_by_label(label, limit=min(total, 1000))
                embedded = sum(1 for e in sample if e.embedding_id)
                report[label.value] = {"total": total, "embedded": embedded}
            return report

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _embed_one(self, entity: KnowledgeEntity) -> None:
        try:
            vector = await self._embed.embed_entity(entity)
            entity.embedding_id = entity.id  # 1:1 mapping convention
            await self._entities.upsert(entity)
            self._log.debug("entity_embedded", entity_id=entity.id)
        except Exception as exc:
            self._log.warning(
                "entity_embedding_failed", entity_id=entity.id, exc=str(exc)
            )