"""IOS Retrieval — Base Retriever."""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.retrieval.interfaces import IRetriever
from app.retrieval.types import MetadataFilter, RetrievedItem


class BaseRetriever(IRetriever):
    """
    Shared foundation for all retriever implementations.

    Provides:
    - Named structured logger
    - OTel span factory
    - Score normalisation to [0.0, 1.0]
    - Content-hash deduplication
    - Metadata filter application (post-fetch, source-agnostic)
    """

    def __init__(self) -> None:
        self._log = get_logger(self.__class__.__module__)

    def _span(self, operation: str, **attrs: str):
        return create_async_span(
            f"retrieval.{self.source.value}.{operation}",
            attributes={"retrieval.source": self.source.value, **attrs},
        )

    # ------------------------------------------------------------------
    # Score normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def normalise_scores(items: list[RetrievedItem]) -> list[RetrievedItem]:
        """
        Min-max normalise raw scores to [0.0, 1.0] in place.

        No-op if all scores are equal or the list is empty/singleton.
        """
        if len(items) < 2:
            return items
        scores = [i.score for i in items]
        lo, hi = min(scores), max(scores)
        if hi == lo:
            for item in items:
                item.score = 1.0
            return items
        for item in items:
            item.score = (item.score - lo) / (hi - lo)
        return items

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def deduplicate(items: list[RetrievedItem]) -> list[RetrievedItem]:
        """
        Remove items with identical content (exact match), keeping the
        highest-scoring instance.
        """
        best_by_content: dict[str, RetrievedItem] = {}
        for item in items:
            key = " ".join(item.content.lower().split())
            existing = best_by_content.get(key)
            if existing is None or item.score > existing.score:
                best_by_content[key] = item
        return list(best_by_content.values())

    # ------------------------------------------------------------------
    # Metadata filtering
    # ------------------------------------------------------------------

    @staticmethod
    def apply_metadata_filter(
        items: list[RetrievedItem], flt: MetadataFilter | None
    ) -> list[RetrievedItem]:
        """Apply a MetadataFilter to a list of retrieved items, post-fetch."""
        if flt is None:
            return items

        result = items
        if flt.tags:
            tag_set = set(flt.tags)
            result = [i for i in result if tag_set.intersection(i.tags)]
        if flt.source_types:
            allowed = set(flt.source_types)
            result = [
                i for i in result
                if i.metadata.get("source_type") in allowed
            ]
        if flt.date_from:
            result = [
                i for i in result
                if i.created_at is not None and i.created_at >= flt.date_from
            ]
        if flt.date_to:
            result = [
                i for i in result
                if i.created_at is not None and i.created_at <= flt.date_to
            ]
        if flt.labels:
            allowed_labels = set(flt.labels)
            result = [
                i for i in result
                if i.metadata.get("label") in allowed_labels
            ]
        return result

    # ------------------------------------------------------------------
    # Confidence estimation
    # ------------------------------------------------------------------

    @staticmethod
    def estimate_confidence(score: float, *, floor: float = 0.0, ceiling: float = 1.0) -> float:
        """Clamp a raw score into a valid confidence range."""
        return max(floor, min(ceiling, score))