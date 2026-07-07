"""IOS Memory — Memory Ranker."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.memory.base import BaseMemoryLayer
from app.memory.types import MemoryRecord, ScoredMemory


@dataclass
class RankingWeights:
    """
    Configurable weights controlling the composite ranking formula.

    All weights are normalised internally so they don't need to sum to 1.0.
    """

    semantic: float = 0.50      # vector similarity contribution
    recency: float = 0.20       # time-decay contribution
    importance: float = 0.20    # stored importance score contribution
    frequency: float = 0.10     # access count contribution (logarithmic)

    # Recency half-life in hours (score halves every N hours)
    recency_half_life_hours: float = 72.0

    def normalised(self) -> "RankingWeights":
        """Return a copy with weights normalised to sum to 1.0."""
        total = self.semantic + self.recency + self.importance + self.frequency
        if total == 0:
            return RankingWeights()
        return RankingWeights(
            semantic=self.semantic / total,
            recency=self.recency / total,
            importance=self.importance / total,
            frequency=self.frequency / total,
            recency_half_life_hours=self.recency_half_life_hours,
        )


class MemoryRanker:
    """
    Composite ranker for memory search results.

    Takes raw ScoredMemory lists (from individual layers) and re-scores
    them using a configurable multi-factor formula.  Designed to be
    stateless — each rank() call is independent.

    Ranking formula (after weight normalisation):

        composite = w_sem  * semantic_score
                  + w_rec  * recency_score
                  + w_imp  * importance_score
                  + w_freq * frequency_score

    All component scores are in [0.0, 1.0].
    """

    def __init__(self, weights: RankingWeights | None = None) -> None:
        self._weights = (weights or RankingWeights()).normalised()

    def rank(
        self,
        candidates: list[ScoredMemory],
        *,
        top_k: int | None = None,
        min_score: float = 0.0,
        deduplicate: bool = True,
    ) -> list[ScoredMemory]:
        """
        Re-score and sort a list of ScoredMemory candidates.

        Args:
            candidates: Raw scored memories from one or more layers.
            top_k: Maximum results to return (None = all).
            min_score: Minimum composite score threshold.
            deduplicate: Remove results pointing to the same record ID.

        Returns:
            Re-scored and sorted list, highest composite score first.
        """
        if not candidates:
            return []

        if deduplicate:
            seen: set[str] = set()
            unique: list[ScoredMemory] = []
            for c in candidates:
                key = str(c.record.id)
                if key not in seen:
                    seen.add(key)
                    unique.append(c)
            candidates = unique

        rescored = [self._rescore(c) for c in candidates]
        filtered = [s for s in rescored if s.score >= min_score]
        filtered.sort(key=lambda s: s.score, reverse=True)
        return filtered[:top_k] if top_k else filtered

    def merge_and_rank(
        self,
        *layer_results: list[ScoredMemory],
        top_k: int | None = None,
        min_score: float = 0.0,
    ) -> list[ScoredMemory]:
        """
        Merge results from multiple layers, re-score, and return ranked list.

        Duplicates across layers (same record ID) are deduplicated by
        keeping the instance with the highest raw semantic score.

        Args:
            *layer_results: One list of ScoredMemory per memory layer.
            top_k: Maximum results.
            min_score: Minimum composite score.

        Returns:
            Merged, deduplicated, re-ranked results.
        """
        merged: dict[str, ScoredMemory] = {}
        for layer in layer_results:
            for sm in layer:
                key = str(sm.record.id)
                if key not in merged:
                    merged[key] = sm
                else:
                    # Keep the one with higher semantic score
                    existing_sem = merged[key].semantic_score or 0.0
                    new_sem = sm.semantic_score or 0.0
                    if new_sem > existing_sem:
                        merged[key] = sm
        return self.rank(
            list(merged.values()),
            top_k=top_k,
            min_score=min_score,
            deduplicate=False,
        )

    # ------------------------------------------------------------------
    # Component scorers
    # ------------------------------------------------------------------

    def _rescore(self, candidate: ScoredMemory) -> ScoredMemory:
        """Compute a fresh composite score using the configured weights."""
        w = self._weights
        rec = candidate.record

        sem  = candidate.semantic_score if candidate.semantic_score is not None else 0.0
        rec_score = self._recency(rec.created_at)
        imp  = max(0.0, min(1.0, rec.importance))
        freq = self._frequency(rec.access_count)

        composite = (
            w.semantic    * sem
            + w.recency   * rec_score
            + w.importance * imp
            + w.frequency  * freq
        )
        # Apply TTL penalty: memories close to expiry get a score reduction
        if rec.expires_at:
            composite *= self._ttl_factor(rec.expires_at)

        return ScoredMemory(
            record=rec,
            score=min(1.0, max(0.0, composite)),
            semantic_score=sem if candidate.semantic_score is not None else None,
            recency_score=rec_score,
            importance_score=imp,
        )

    def _recency(self, created_at: datetime) -> float:
        """Exponential decay score based on age."""
        now = datetime.now(tz=timezone.utc)
        age_hours = (now - created_at).total_seconds() / 3600.0
        return math.exp(
            -math.log(2) * age_hours / self._weights.recency_half_life_hours
        )

    @staticmethod
    def _frequency(access_count: int) -> float:
        """Log-compressed frequency score; 0 accesses → 0.0, saturates near 1.0."""
        if access_count <= 0:
            return 0.0
        return min(1.0, math.log1p(access_count) / math.log1p(100))

    @staticmethod
    def _ttl_factor(expires_at: datetime) -> float:
        """
        Penalty multiplier for memories approaching expiry.

        Returns 1.0 when expiry is far away; approaches 0.1 as expiry nears.
        """
        remaining_hours = (expires_at - datetime.now(tz=timezone.utc)).total_seconds() / 3600
        if remaining_hours <= 0:
            return 0.0
        if remaining_hours >= 24:
            return 1.0
        # Linear fade over final 24 hours
        return max(0.1, remaining_hours / 24.0)

    # ------------------------------------------------------------------
    # Weight factory helpers
    # ------------------------------------------------------------------

    @classmethod
    def for_context_injection(cls) -> "MemoryRanker":
        """Weights optimised for injecting context into LLM prompts."""
        return cls(RankingWeights(semantic=0.55, recency=0.25, importance=0.15, frequency=0.05))

    @classmethod
    def for_experience_retrieval(cls) -> "MemoryRanker":
        """Weights optimised for retrieving past task experiences."""
        return cls(RankingWeights(semantic=0.60, recency=0.10, importance=0.25, frequency=0.05))

    @classmethod
    def for_knowledge_retrieval(cls) -> "MemoryRanker":
        """Weights optimised for semantic knowledge lookup."""
        return cls(RankingWeights(semantic=0.70, recency=0.05, importance=0.20, frequency=0.05))