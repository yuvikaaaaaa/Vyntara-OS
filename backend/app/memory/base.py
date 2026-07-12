"""IOS Memory — Base Layer."""
from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from uuid import UUID

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.memory.exceptions import MemoryExpiredError
from app.memory.interfaces import IMemoryLayer
from app.memory.types import MemoryLayerType, MemoryRecord, ScoredMemory


class BaseMemoryLayer(IMemoryLayer):
    def __init__(self) -> None:
        self._log = get_logger(self.__class__.__module__)

    def _span(self, operation: str, **attrs: str):
        return create_async_span(
            f"memory.{self.layer_type.value}.{operation}",
            attributes={"memory.layer": self.layer_type.value, **attrs},
        )

    def _assert_not_expired(self, record: MemoryRecord) -> None:
        if record.is_expired:
            raise MemoryExpiredError(
                f"Memory record {record.id} has expired.",
                details={"id": str(record.id)},
            )

    @staticmethod
    def recency_score(created_at: datetime, *, half_life_hours: float = 72.0) -> float:
        now = datetime.now(tz=timezone.utc)
        age_hours = (now - created_at).total_seconds() / 3600.0
        return math.exp(-math.log(2) * age_hours / half_life_hours)

    @staticmethod
    def importance_score(importance: float) -> float:
        return max(0.0, min(1.0, importance))

    @classmethod
    def composite_score(
        cls,
        record: MemoryRecord,
        *,
        semantic_score: float | None = None,
        semantic_weight: float = 0.7,
    ) -> ScoredMemory:
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

    @staticmethod
    def content_fingerprint(content: str) -> str:
        normalised = " ".join(content.lower().split())
        return hashlib.sha256(normalised.encode()).hexdigest()

    @staticmethod
    def decayed_importance(current: float, *, days_elapsed: float, decay_rate: float = 0.05) -> float:
        return current * math.exp(-decay_rate * days_elapsed)

    @staticmethod
    def estimate_tokens(text: str) -> int:
        return max(1, len(text) // 4)