"""IOS Retrieval — Graph Retriever."""
from __future__ import annotations

from app.knowledge.knowledge_search import KnowledgeSearch
from app.knowledge.types import EntityLabel, KnowledgeSearchRequest, KnowledgeSearchStrategy
from app.retrieval.base import BaseRetriever
from app.retrieval.types import RetrievalRequest, RetrievalSource, RetrievedItem


class GraphRetriever(BaseRetriever):
    """
    Knowledge-graph retrieval adapter.

    Wraps the Knowledge Engine's ``KnowledgeSearch`` facade and normalises
    its ``KnowledgeSearchResult`` output into ``RetrievedItem`` so it can
    participate in hybrid retrieval alongside vector and memory sources.

    Never raises on graph unavailability — returns an empty list and logs
    a warning, matching the resilience contract of VectorRetriever.
    """

    def __init__(
        self,
        knowledge_search: KnowledgeSearch,
        *,
        default_strategy: KnowledgeSearchStrategy = KnowledgeSearchStrategy.HYBRID,
        traversal_depth: int = 1,
    ) -> None:
        super().__init__()
        self._search = knowledge_search
        self._default_strategy = default_strategy
        self._traversal_depth = traversal_depth

    @property
    def source(self) -> RetrievalSource:
        return RetrievalSource.GRAPH

    async def retrieve(self, request: RetrievalRequest) -> list[RetrievedItem]:
        async with self._span("retrieve", top_k=str(request.top_k)):
            labels = None
            if request.metadata_filter and request.metadata_filter.labels:
                labels = [
                    EntityLabel(lbl)
                    for lbl in request.metadata_filter.labels
                    if lbl in EntityLabel._value2member_map_
                ] or None

            kg_request = KnowledgeSearchRequest(
                query=request.query,
                strategy=self._default_strategy,
                labels=labels,
                top_k=request.top_k * 2,
                min_confidence=request.min_score,
                include_relations=True,
                traversal_depth=self._traversal_depth,
            )

            try:
                response = await self._search.search(kg_request)
            except Exception as exc:
                self._log.warning("graph_retrieval_failed", exc=str(exc))
                return []

            items = [self._result_to_item(r) for r in response.results]
            items = self.deduplicate(items)
            items = self.apply_metadata_filter(items, request.metadata_filter)
            items.sort(key=lambda i: i.score, reverse=True)

            self._log.info(
                "graph_retrieval_complete",
                query_len=len(request.query),
                results=len(items),
            )
            return items[: request.top_k]

    async def health_check(self) -> bool:
        try:
            # A trivial fulltext search doubles as a connectivity probe.
            from app.knowledge.types import KnowledgeSearchRequest as _Req
            await self._search.search(
                _Req(query="health_check_probe", top_k=1)
            )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _result_to_item(result) -> RetrievedItem:
        entity = result.entity
        relation_summary = ", ".join(
            f"{r.relation_type.value}->{r.to_id}" for r in (result.relations or [])[:5]
        )
        content_parts = [entity.name]
        if entity.description:
            content_parts.append(entity.description)
        if relation_summary:
            content_parts.append(f"Related: {relation_summary}")
        content = ". ".join(content_parts)

        return RetrievedItem(
            id=entity.id,
            content=content,
            source=RetrievalSource.GRAPH,
            score=result.score,
            confidence=entity.confidence,
            title=entity.name,
            metadata={
                "label": entity.label.value,
                "source_type": entity.source_type,
                "version": entity.version,
                "relation_count": len(result.relations or []),
            },
            tags=entity.tags,
            created_at=entity.created_at,
            parent_id=entity.source_id,
        )