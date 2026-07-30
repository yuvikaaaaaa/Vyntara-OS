"""IOS Tools — Base."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, TypeVar

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.tools.exceptions import ToolTimeoutError

F = TypeVar("F", bound=Callable[..., Any])


class BaseToolComponent:
    """
    Shared foundation for all Tool Execution Framework components.

    Provides:
    - Named structured logger
    - OTel async span factory (execution tracing)
    - Timing helpers
    - Retry-with-backoff helper
    - Timeout enforcement wrapper
    - Lightweight in-memory metrics accumulator (EMA-based)
    """

    def __init__(self) -> None:
        self._log = get_logger(self.__class__.__module__)
        self._metrics: dict[str, int | float] = {}

    def _span(self, operation: str, **attrs: str):
        return create_async_span(
            f"tools.{operation}",
            attributes={"tools.component": self.__class__.__name__, **attrs},
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
    # Timeout
    # ------------------------------------------------------------------

    async def _with_timeout(self, coro, timeout_seconds: float, *, label: str = ""):
        try:
            return await asyncio.wait_for(coro, timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise ToolTimeoutError(
                f"Operation '{label}' timed out after {timeout_seconds}s.",
                details={"label": label, "timeout_seconds": timeout_seconds},
            ) from exc

    # ------------------------------------------------------------------
    # Retry
    # ------------------------------------------------------------------

    async def _with_retry(
        self,
        coro_factory: Callable[[], Any],
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
        label: str = "",
    ):
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await coro_factory()
            except retryable_exceptions as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    self._log.warning(
                        "tool_retry",
                        label=label,
                        attempt=attempt + 1,
                        delay=delay,
                        exc=str(exc),
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _incr(self, key: str, amount: int | float = 1) -> None:
        self._metrics[key] = self._metrics.get(key, 0) + amount

    def _ema(self, key: str, value: float, *, alpha: float = 0.1) -> float:
        prev = self._metrics.get(key)
        updated = value if prev is None else prev * (1 - alpha) + value * alpha
        self._metrics[key] = updated
        return updated

    def get_metrics_snapshot(self) -> dict[str, int | float]:
        return dict(self._metrics)