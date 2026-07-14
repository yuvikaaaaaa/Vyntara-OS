"""IOS Knowledge — Base."""
from __future__ import annotations

import re
import unicodedata

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.knowledge.exceptions import ConfidenceTooLowError
from app.knowledge.types import KnowledgeEntity, ValidationResult, ValidationSeverity, ValidationIssue


class BaseKnowledge:
    """
    Shared foundation for all knowledge engine components.

    Provides:
    - Named structured logger
    - OTel async span factory
    - Confidence threshold guard
    - Entity name normalisation
    - Simple property diff for merge conflict detection
    """

    #: Minimum confidence accepted by default guards
    MIN_CONFIDENCE: float = 0.0

    def __init__(self) -> None:
        self._log = get_logger(self.__class__.__module__)

    def _span(self, operation: str, **attrs: str):
        return create_async_span(
            f"knowledge.{operation}",
            attributes={"knowledge.component": self.__class__.__name__, **attrs},
        )

    def _assert_confidence(
        self, confidence: float, *, threshold: float | None = None
    ) -> None:
        limit = threshold if threshold is not None else self.MIN_CONFIDENCE
        if confidence < limit:
            raise ConfidenceTooLowError(
                f"Confidence {confidence:.2f} is below the required threshold {limit:.2f}.",
                details={"confidence": confidence, "threshold": limit},
            )

    @staticmethod
    def normalise_name(name: str) -> str:
        """
        Normalise an entity name for consistent comparison and deduplication.

        Steps: unicode NFKC → strip → collapse whitespace → lower-case.
        """
        normalised = unicodedata.normalize("NFKC", name)
        normalised = re.sub(r"\s+", " ", normalised).strip().lower()
        return normalised

    @staticmethod
    def property_diff(
        existing: dict, incoming: dict
    ) -> dict[str, tuple]:
        """
        Return properties that differ between existing and incoming dicts.

        Returns:
            {key: (existing_value, incoming_value)} for conflicting keys.
        """
        conflicts: dict[str, tuple] = {}
        for key, new_val in incoming.items():
            old_val = existing.get(key)
            if old_val is not None and old_val != new_val:
                conflicts[key] = (old_val, new_val)
        return conflicts

    @staticmethod
    def merge_confidence(c1: float, c2: float, *, strategy: str = "max") -> float:
        """
        Combine two confidence scores.

        Strategies:
        - ``max``: take the higher of the two (default)
        - ``avg``: arithmetic mean
        - ``product``: probabilistic AND (c1 * c2)
        """
        if strategy == "avg":
            return (c1 + c2) / 2.0
        if strategy == "product":
            return c1 * c2
        return max(c1, c2)

    @staticmethod
    def build_validation_error(
        code: str, message: str, field: str | None = None, entity_id: str | None = None
    ) -> ValidationIssue:
        return ValidationIssue(
            severity=ValidationSeverity.ERROR,
            code=code,
            message=message,
            field=field,
            entity_id=entity_id,
        )

    @staticmethod
    def build_validation_warning(
        code: str, message: str, field: str | None = None, entity_id: str | None = None
    ) -> ValidationIssue:
        return ValidationIssue(
            severity=ValidationSeverity.WARNING,
            code=code,
            message=message,
            field=field,
            entity_id=entity_id,
        )

    @staticmethod
    def ok() -> ValidationResult:
        return ValidationResult(is_valid=True)