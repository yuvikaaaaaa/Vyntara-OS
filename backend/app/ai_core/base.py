"""IOS AI Core — Base Utilities."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, TypeVar

from app.ai_core.exceptions import ProviderTimeoutError, ProviderUnavailableError
from app.core.logging import get_logger
from app.core.telemetry import create_async_span

F = TypeVar("F", bound=Callable[..., Any])


class AICoreMixin:
    """
    Shared utilities for all provider implementations.

    Providers inherit from this mixin alongside the interface ABC.
    Provides:
    - Named structured logger
    - OTel span factory
    - Retry with exponential backoff
    - Timeout enforcement
    - Latency measurement
    """

    def __init__(self) -> None:
        self._log = get_logger(self.__class__.__module__)

    def _span(self, name: str, **attrs: str):
        """Return an OTel async span context manager."""
        return create_async_span(
            f"ai_core.{name}",
            attributes={k: v for k, v in attrs.items()},
        )

    async def _with_timeout(
        self,
        coro,
        timeout: float,
        provider: str,
        model_id: str,
    ):
        """Wrap a coroutine with a timeout, raising ProviderTimeoutError."""
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError(
                f"Provider '{provider}' timed out after {timeout}s for model '{model_id}'.",
                details={"provider": provider, "model_id": model_id, "timeout": timeout},
            ) from exc

    async def _with_retry(
        self,
        coro_factory: Callable[[], Any],
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        provider: str = "",
        operation: str = "",
    ):
        """
        Retry a coroutine factory with exponential backoff.

        Retries on ProviderUnavailableError and asyncio.TimeoutError.
        Re-raises on the final attempt.
        """
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await coro_factory()
            except (ProviderUnavailableError, asyncio.TimeoutError) as exc:
                last_exc = exc
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    self._log.warning(
                        "ai_core_retry",
                        provider=provider,
                        operation=operation,
                        attempt=attempt + 1,
                        delay=delay,
                        exc=str(exc),
                    )
                    await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    @staticmethod
    def _now_ms() -> int:
        """Current time in milliseconds."""
        return int(time.perf_counter() * 1000)

    @staticmethod
    def _elapsed_ms(start_ms: int) -> int:
        return int(time.perf_counter() * 1000) - start_ms