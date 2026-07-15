"""IOS Knowledge — Knowledge Search."""
from __future__ import annotations

from app.knowledge.base import BaseKnowledge
from app.knowledge.exceptions import KnowledgeSearchError
from app.knowledge.interfaces import IEntityStore, IGraphTraverser, IKnowledgeEmbeddingGateway, IRelationStore
from app.knowledge.types import (
    EntityLabel,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResult,
    KnowledgeSearchStrategy,
    TraversalDirection,
)


class KnowledgeSearch(BaseKnowledge):
    """
    Unified search facade over the knowledge graph.

    Supports:
    - FULLTEXT: Neo4j full-text index search on name/description
    - GRAPH_TRAVERSAL: expand from an anchor entity found via fulltext
    - SEMANTIC: vector similarity via an embedding gateway (optional)
    - HYBRID: fulltext + semantic merged and re-ranked

    Falls back gracefully to fulltext when no embedding gateway is
    configured, so the search facade never hard-fails on missing
    infrastructure.
    """

    def __init__(
        self,
        entity_store: IEntityStore,
        relation_store: IRelationStore,
        traverser: IGraphTraverser,
        embedding_gateway: IKnowledgeEmbeddingGateway | None = None,
    ) -> None:
        super().__init__()
        self._entities = entity_store
        self._relations = relation_store
        self._traverser = traverser
        self._embed = embedding_gateway

    # ------------------------------------------------------------------
    # Primary search API
    # ------------------------------------------------------------------

    async def search(self, request: KnowledgeSearchRequest) -> KnowledgeSearchResponse:
        """
        Execute a knowledge search using the requested strategy.

        Raises:
            KnowledgeSearchError: On search execution failure.
        """
        async with self._span("search", strategy=request.strategy.value):
            try:
                if request.strategy == KnowledgeSearchStrategy.FULLTEXT:
                    results = await self._fulltext_search(request)
                elif request.strategy == KnowledgeSearchStrategy.GRAPH_TRAVERSAL:
                    results = await self._traversal_search(request)
                elif request.strategy == KnowledgeSearchStrategy.SEMANTIC:
                    results = await self._semantic_search(request)
                else:  # HYBRID
                    results = await self._hybrid_search(request)
            except Exception as exc:
                raise KnowledgeSearchError(
                    f"Search failed for query '{request.query}': {exc}",
                    details={"strategy": request.strategy.value},
                ) from exc

            filtered = [r for r in results if r.entity.confidence >= request.min_confidence]
            paginated = filtered[: request.top_k]

            if request.include_relations:
                for r in paginated:
                    r.relations = await self._relations.get_relations(
                        r.entity.id, direction=TraversalDirection.BOTH
                    )

            self._log.info(
                "knowledge_search_complete",
                query=request.query[:80],
                strategy=request.strategy.value,
                total=len(filtered),
                returned=len(paginated),
            )
            return KnowledgeSearchResponse(
                results=paginated,
                total_found=len(filtered),
                query=request.query,
                strategy=request.strategy,
            )

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    async def _fulltext_search(
        self, request: KnowledgeSearchRequest
    ) -> list[KnowledgeSearchResult]:
        entities = await self._entities.fulltext_search(
            request.query, labels=request.labels, limit=request.top_k * 2
        )
        return [
            KnowledgeSearchResult(entity=e, score=e.confidence)
            for e in entities
        ]

    async def _traversal_search(
        self, request: KnowledgeSearchRequest
    ) -> list[KnowledgeSearchResult]:
        # Find anchor entities via fulltext, then expand each via traversal
        anchors = await self._entities.fulltext_search(
            request.query, labels=request.labels, limit=3
        )
        results: list[KnowledgeSearchResult] = []
        seen: set[str] = set()
        for anchor in anchors:
            if anchor.id not in seen:
                results.append(KnowledgeSearchResult(entity=anchor, score=1.0))
                seen.add(anchor.id)
            traversal = await self._traverser.traverse(
                anchor.id,
                direction=TraversalDirection.BOTH,
                max_depth=request.traversal_depth,
                limit=request.top_k * 2,
            )
            for node in traversal.nodes:
                if node.id not in seen:
                    # Decay score with traversal distance from anchor
                    results.append(KnowledgeSearchResult(entity=node, score=0.7))
                    seen.add(node.id)
        return results

    async def _semantic_search(
        self, request: KnowledgeSearchRequest
    ) -> list[KnowledgeSearchResult]:
        if not self._embed:
            self._log.warning("semantic_search_no_gateway", query=request.query[:80])
            return await self._fulltext_search(request)

        hits = await self._embed.search_similar(
            request.query, top_k=request.top_k * 2, min_score=request.min_confidence
        )
        results: list[KnowledgeSearchResult] = []
        for entity_id, score in hits:
            entity = await self._entities.get(entity_id)
            if entity is None:
                continue
            if request.labels and entity.label not in request.labels:
                continue
            results.append(KnowledgeSearchResult(entity=entity, score=score))
        return results

    async def _hybrid_search(
        self, request: KnowledgeSearchRequest
    ) -> list[KnowledgeSearchResult]:
        fulltext_results = await self._fulltext_search(request)
        semantic_results = (
            await self._semantic_search(request) if self._embed else []
        )

        merged: dict[str, KnowledgeSearchResult] = {}
        for r in fulltext_results:
            merged[r.entity.id] = KnowledgeSearchResult(
                entity=r.entity, score=r.score * 0.4
            )
        for r in semantic_results:
            if r.entity.id in merged:
                merged[r.entity.id].score += r.score * 0.6
            else:
                merged[r.entity.id] = KnowledgeSearchResult(
                    entity=r.entity, score=r.score * 0.6
                )

        return sorted(merged.values(), key=lambda r: r.score, reverse=True)

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    async def find_entity_by_name(
        self, name: str, label: EntityLabel
    ) -> KnowledgeSearchResult | None:
        entity = await self._entities.get_by_name(name, label)
        if entity is None:
            return None
        return KnowledgeSearchResult(entity=entity, score=1.0)

    async def search_within_neighbourhood(
        self,
        anchor_id: str,
        query: str,
        *,
        depth: int = 2,
        top_k: int = 10,
    ) -> list[KnowledgeSearchResult]:
        """Search restricted to the graph neighbourhood of an anchor entity."""
        neighbours = await self._traverser.get_neighbours(anchor_id, depth=depth, limit=200)
        query_lower = query.lower()
        scored = [
            KnowledgeSearchResult(
                entity=n,
                score=1.0 if query_lower in n.name.lower() else 0.5,
            )
            for n in neighbours
            if query_lower in n.name.lower() or query_lower in (n.description or "").lower()
        ]
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]