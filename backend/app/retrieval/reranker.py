"""IOS Retrieval — Re-Ranker."""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.retrieval.exceptions import RerankingError
from app.retrieval.interfaces import IReranker
from app.retrieval.types import RerankedItem, RetrievalSource, RetrievedItem

logger = get_logger(__name__)

# Default per-source trust weights used in heuristic fusion.
# Vector search tends to have the tightest semantic precision; graph
# retrieval carries strong structural signal; memory sources are weighted
# slightly lower as they reflect the user's own history rather than
# curated knowledge.
_DEFAULT_SOURCE_WEIGHTS: dict[RetrievalSource, float] = {
    RetrievalSource.VECTOR: 1.0,
    RetrievalSource.GRAPH: 0.9,
    RetrievalSource.MEMORY_EPISODIC: 0.75,
    RetrievalSource.MEMORY_SEMANTIC: 0.85,
    RetrievalSource.MEMORY_WORKING: 0.6,
}


class ReRanker(IReranker):
    """
    Cross-source re-ranker for merged retrieval candidates.

    Default strategy is a heuristic weighted-score fusion:

        rerank_score = source_weight * normalised_score
                     + confidence_bonus * confidence
                     + recency_bonus (if created_at present)

    An optional LLM-based relevance scorer can be supplied to replace or
    augment the heuristic — useful once AI Core exposes a proper
    cross-encoder-equivalent scoring endpoint.
    """

    def __init__(
        self,
        *,
        source_weights: dict[RetrievalSource, float] | None = None,
        confidence_weight: float = 0.15,
        recency_weight: float = 0.10,
        llm_scorer=None,   # Optional async callable (query, item) -> float
    ) -> None:
        self._weights = source_weights or dict(_DEFAULT_SOURCE_WEIGHTS)
        self._confidence_weight = confidence_weight
        self._recency_weight = recency_weight
        self._llm_scorer = llm_scorer
        self._log = logger

    async def rerank(
        self,
        query: str,
        items: list[RetrievedItem],
        *,
        top_k: int | None = None,
    ) -> list[RerankedItem]:
        async with create_async_span(
            "retrieval.rerank", attributes={"candidates": str(len(items))}
        ):
            if not items:
                return []

            try:
                scored = await self._score_all(query, items)
            except Exception as exc:
                raise RerankingError(f"Re-ranking failed: {exc}") from exc

            scored.sort(key=lambda ri: ri.rerank_score, reverse=True)
            for final_rank, ri in enumerate(scored):
                ri.final_rank = final_rank

            result = scored[:top_k] if top_k else scored
            self._log.info(
                "rerank_complete", candidates=len(items), returned=len(result)
            )
            return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _score_all(
        self, query: str, items: list[RetrievedItem]
    ) -> list[RerankedItem]:
        scored: list[RerankedItem] = []
        for original_rank, item in enumerate(items):
            heuristic = self._heuristic_score(item)
            final_score = heuristic

            if self._llm_scorer is not None:
                try:
                    llm_score = await self._llm_scorer(query, item)
                    final_score = 0.5 * heuristic + 0.5 * llm_score
                except Exception as exc:
                    self._log.warning(
                        "llm_rerank_scorer_failed", item_id=item.id, exc=str(exc)
                    )

            scored.append(
                RerankedItem(
                    item=item,
                    rerank_score=final_score,
                    original_rank=original_rank,
                    final_rank=original_rank,  # overwritten after sort
                )
            )
        return scored

    def _heuristic_score(self, item: RetrievedItem) -> float:
        source_weight = self._weights.get(item.source, 0.5)
        base = source_weight * item.score
        confidence_bonus = self._confidence_weight * item.confidence

        recency_bonus = 0.0
        if item.created_at is not None:
            recency_bonus = self._recency_weight * self._recency_factor(item.created_at)

        return min(1.0, base + confidence_bonus + recency_bonus)

    @staticmethod
    def _recency_factor(created_at) -> float:
        """Exponential decay recency factor, half-life 7 days."""
        import math
        from datetime import datetime, timezone

        age_days = (datetime.now(tz=timezone.utc) - created_at).total_seconds() / 86_400
        return math.exp(-math.log(2) * age_days / 7.0)