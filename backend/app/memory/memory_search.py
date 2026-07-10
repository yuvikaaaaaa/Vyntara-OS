"""IOS Memory — Memory Search."""
from __future__ import annotations

import asyncio
from uuid import UUID

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.memory.interfaces import IMemoryLayer
from app.memory.memory_ranker import MemoryRanker
from app.memory.types import (
    MemoryLayerType,
    MemorySearchRequest,
    MemorySearchResponse,
    MemorySearchResult,
    ScoredMemory,
    SearchStrategy,
)

logger = get_logger(__name__)


class MemorySearch:
    """
    Unified cross-layer memory search engine.

    Fans a query out to every registered memory layer in parallel,
    merges the scored results, re-ranks with a configurable MemoryRanker,
    applies tag / date filters, and paginates.

    Layers are registered at construction time and must implement IMemoryLayer.
    """

    def __init__(
        self,
        layers: dict[MemoryLayerType, IMemoryLayer],
        ranker: MemoryRanker | None = None,
    ) -> None:
        self._layers = layers
        self._ranker = ranker or MemoryRanker()

    # ------------------------------------------------------------------
    # Primary search API
    # ------------------------------------------------------------------

    async def search(
        self, request: MemorySearchRequest
    ) -> MemorySearchResponse:
        """
        Execute a cross-layer memory search.

        Steps:
        1. Fan query to all requested layers concurrently.
        2. Merge scored results; deduplicate by record ID.
        3. Apply optional post-filters (tags, date range).
        4. Re-rank with MemoryRanker.
        5. Paginate to top_k.

        Args:
            request: Full search specification.

        Returns:
            MemorySearchResponse with ranked, paginated results.
        """
        async with create_async_span(
            "memory_search.search",
            attributes={
                "layers": ",".join(layer.value for layer in request.layers),
                "strategy": request.strategy.value,
                "top_k": str(request.top_k),
            },
        ):
            target_layers = {
                lt: layer
                for lt, layer in self._layers.items()
                if lt in request.layers
            }

            if not target_layers:
                return MemorySearchResponse(
                    results=[],
                    total_found=0,
                    layers_searched=[],
                    query=request.query,
                )

            # Parallel fan-out
            layer_results = await self._fan_out(request, target_layers)

            # Merge via ranker
            all_scored: list[ScoredMemory] = []
            for scored_list in layer_results.values():
                all_scored.extend(scored_list)

            ranked = self._ranker.merge_and_rank(
                *layer_results.values(),
                top_k=request.top_k * 3,   # over-fetch before filtering
                min_score=request.min_score,
            )

            # Post-filter
            filtered = self._apply_filters(ranked, request)

            # Build response
            results = [
                MemorySearchResult(
                    record=sm,
                    layer=sm.record.layer,
                    highlighted_content=self._highlight(sm.record.content, request.query),
                )
                for sm in filtered[: request.top_k]
            ]
            logger.info(
                "memory_search_complete",
                query_len=len(request.query),
                layers=list(target_layers.keys()),
                total=len(filtered),
                returned=len(results),
            )
            return MemorySearchResponse(
                results=results,
                total_found=len(filtered),
                layers_searched=list(target_layers.keys()),
                query=request.query,
            )

    async def search_layer(
        self,
        layer: MemoryLayerType,
        request: MemorySearchRequest,
    ) -> list[ScoredMemory]:
        """Search a single named layer (no cross-layer merge)."""
        target = self._layers.get(layer)
        if not target:
            return []
        request = MemorySearchRequest(
            query=request.query,
            user_id=request.user_id,
            layers=[layer],
            strategy=request.strategy,
            top_k=request.top_k,
            min_score=request.min_score,
            semantic_weight=request.semantic_weight,
            tags_filter=request.tags_filter,
            exclude_expired=request.exclude_expired,
        )
        raw = await target.search(request)
        return self._ranker.rank(raw, top_k=request.top_k, min_score=request.min_score)

    async def get_similar_experiences(
        self,
        user_id: UUID,
        query: str,
        *,
        top_k: int = 5,
        min_score: float = 0.3,
    ) -> list[MemorySearchResult]:
        """
        Convenience: find episodic memories similar to a query.

        Used by agents before task execution to retrieve few-shot examples.
        """
        request = MemorySearchRequest(
            query=query,
            user_id=user_id,
            layers=[MemoryLayerType.EPISODIC],
            strategy=SearchStrategy.SEMANTIC,
            top_k=top_k,
            min_score=min_score,
        )
        resp = await self.search(request)
        return resp.results

    async def get_relevant_knowledge(
        self,
        user_id: UUID,
        query: str,
        *,
        top_k: int = 10,
        min_score: float = 0.35,
        tags: list[str] | None = None,
    ) -> list[MemorySearchResult]:
        """
        Convenience: find semantic memories relevant to a query.

        Used by agents during RAG context injection.
        """
        request = MemorySearchRequest(
            query=query,
            user_id=user_id,
            layers=[MemoryLayerType.SEMANTIC],
            strategy=SearchStrategy.HYBRID,
            top_k=top_k,
            min_score=min_score,
            tags_filter=tags,
        )
        resp = await self.search(request)
        return resp.results

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fan_out(
        self,
        request: MemorySearchRequest,
        targets: dict[MemoryLayerType, IMemoryLayer],
    ) -> dict[MemoryLayerType, list[ScoredMemory]]:
        """Execute per-layer searches concurrently."""
        async def _search_one(
            layer_type: MemoryLayerType, layer: IMemoryLayer
        ) -> tuple[MemoryLayerType, list[ScoredMemory]]:
            try:
                results = await layer.search(request)
                return layer_type, results
            except Exception as exc:
                logger.warning(
                    "memory_layer_search_failed",
                    layer=layer_type.value,
                    exc=str(exc),
                )
                return layer_type, []

        tasks = [_search_one(lt, layer) for lt, layer in targets.items()]
        gathered = await asyncio.gather(*tasks)
        return dict(gathered)

    @staticmethod
    def _apply_filters(
        scored: list[ScoredMemory],
        request: MemorySearchRequest,
    ) -> list[ScoredMemory]:
        """Apply tag and TTL post-filters after ranking."""
        result = scored
        if request.exclude_expired:
            result = [s for s in result if not s.record.is_expired]
        if request.tags_filter:
            tag_set = set(request.tags_filter)
            result = [
                s for s in result
                if tag_set.intersection(s.record.tags)
            ]
        return result

    @staticmethod
    def _highlight(content: str, query: str, *, max_length: int = 200) -> str:
        """Extract the most relevant excerpt from content matching the query."""
        query_words = {w.lower() for w in query.split() if len(w) > 3}
        if not query_words:
            return content[:max_length]

        sentences = content.replace("\n", " ").split(". ")
        best: tuple[int, str] = (0, sentences[0] if sentences else content[:max_length])

        for sentence in sentences:
            matches = sum(1 for w in query_words if w in sentence.lower())
            if matches > best[0]:
                best = (matches, sentence)

        excerpt = best[1]
        return excerpt[:max_length] + ("…" if len(excerpt) > max_length else "")