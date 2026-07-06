"""IOS Memory — Base Layer."""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from uuid import UUID

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.memory.exceptions import MemoryExpiredError, MemoryNotFoundError
from app.memory.interfaces import IMemoryLayer
from app.memory.types import MemoryLayerType, MemoryRecord, ScoredMemory


class BaseMemoryLayer(IMemoryLayer):
    """
    Shared foundation for all memory layer implementations.

    Provides:
    - Named structured logger
    - OTel span factory
    - TTL expiry checking
    - Importance decay computation
    - Recency score computation (exponential decay)
    - Content fingerprinting for duplicate detection
    - Composite score computation for ranking
    """

    def __init__(self) -> None:
        self._log = get_logger(self.__class__.__module__)

    # ------------------------------------------------------------------
    # OTel helpers
    # ------------------------------------------------------------------

    def _span(self, operation: str, **attrs: str):
        return create_async_span(
            f"memory.{self.layer_type.value}.{operation}",
            attributes={"memory.layer": self.layer_type.value, **attrs},
        )

    # ------------------------------------------------------------------
    # TTL
    # ------------------------------------------------------------------

    def _assert_not_expired(self, record: MemoryRecord) -> None:
        if record.is_expired:
            raise MemoryExpiredError(
                f"Memory record {record.id} has expired.",
                details={"id": str(record.id), "expired_at": str(record.expires_at)},
            )

    # ------------------------------------------------------------------
    # Scoring primitives
    # ------------------------------------------------------------------

    @staticmethod
    def recency_score(created_at: datetime, *, half_life_hours: float = 72.0) -> float:
        """
        Exponential decay score: 1.0 when just created, approaches 0 as age grows.

        half_life_hours: time (hours) after which the score halves.
        """
        now = datetime.now(tz=timezone.utc)
        age_hours = (now - created_at).total_seconds() / 3600.0
        return math.exp(-math.log(2) * age_hours / half_life_hours)

    @staticmethod
    def importance_score(importance: float) -> float:
        """Pass-through with clamping to [0.0, 1.0]."""
        return max(0.0, min(1.0, importance))

    @classmethod
    def composite_score(
        cls,
        record: MemoryRecord,
        *,
        semantic_score: float | None = None,
        semantic_weight: float = 0.7,
    ) -> ScoredMemory:
        """
        Build a ScoredMemory with a composite score.

        score = semantic_weight * semantic + (1 - semantic_weight) * recency
                + 0.1 * importance bonus
        """
        rec = cls.recency_score(record.created_at)
        imp = cls.importance_score(record.importance)
        sem = semantic_score or 0.0

        if semantic_score is not None:
            base = semantic_weight * sem + (1 - semantic_weight) * rec
        else:
            base = rec

        composite = min(1.0, base + 0.1 * imp)
        return ScoredMemory(
            record=record,
            score=composite,
            semantic_score=sem if semantic_score is not None else None,
            recency_score=rec,
            importance_score=imp,
        )

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    @staticmethod
    def content_fingerprint(content: str) -> str:
        """SHA-256 fingerprint of normalised content for duplicate detection."""
        normalised = " ".join(content.lower().split())
        return hashlib.sha256(normalised.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Importance decay
    # ------------------------------------------------------------------

    @staticmethod
    def decayed_importance(
        current_importance: float,
        *,
        days_elapsed: float,
        decay_rate: float = 0.05,
    ) -> float:
        """
        Reduce importance exponentially over time.

        decayed = importance * e^(-decay_rate * days_elapsed)
        """
        return current_importance * math.exp(-decay_rate * days_elapsed)

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Approximate token count (~4 chars per token for English)."""
        return max(1, len(text) // 4)