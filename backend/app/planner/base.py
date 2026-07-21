"""IOS Planner — Base."""
from __future__ import annotations

import time
from dataclasses import asdict, is_dataclass
from typing import Any

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.planner.types import ValidationIssue, ValidationSeverity


class BasePlanner:
    """
    Shared foundation for all Planning Engine components.

    Provides:
    - Named structured logger
    - OTel async span factory
    - Wall-clock timing helper
    - Plan/dataclass JSON-safe serialization
    - Token estimation (consistent heuristic across the module)
    - Common validation-issue builders
    """

    CHARS_PER_TOKEN: float = 4.0

    def __init__(self) -> None:
        self._log = get_logger(self.__class__.__module__)

    def _span(self, operation: str, **attrs: str):
        return create_async_span(
            f"planner.{operation}",
            attributes={"planner.component": self.__class__.__name__, **attrs},
        )

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------

    @staticmethod
    def _now_ms() -> int:
        return int(time.perf_counter() * 1000)

    @staticmethod
    def _elapsed_ms(start_ms: int) -> int:
        return int(time.perf_counter() * 1000) - start_ms

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    @staticmethod
    def to_dict(obj: Any) -> Any:
        """Recursively convert a dataclass (or nested structure) to a plain dict."""
        if is_dataclass(obj) and not isinstance(obj, type):
            return {k: BasePlanner.to_dict(v) for k, v in asdict(obj).items()}
        if isinstance(obj, list):
            return [BasePlanner.to_dict(v) for v in obj]
        if isinstance(obj, dict):
            return {k: BasePlanner.to_dict(v) for k, v in obj.items()}
        return obj

    # ------------------------------------------------------------------
    # Token estimation
    # ------------------------------------------------------------------

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        return max(1, int(len(text) / cls.CHARS_PER_TOKEN))

    # ------------------------------------------------------------------
    # Validation issue builders
    # ------------------------------------------------------------------

    @staticmethod
    def error(code: str, message: str, task_id: str | None = None) -> ValidationIssue:
        return ValidationIssue(
            severity=ValidationSeverity.ERROR, code=code, message=message, task_id=task_id
        )

    @staticmethod
    def warning(code: str, message: str, task_id: str | None = None) -> ValidationIssue:
        return ValidationIssue(
            severity=ValidationSeverity.WARNING, code=code, message=message, task_id=task_id
        )

    @staticmethod
    def info(code: str, message: str, task_id: str | None = None) -> ValidationIssue:
        return ValidationIssue(
            severity=ValidationSeverity.INFO, code=code, message=message, task_id=task_id
        )