"""IOS Tools Plugins — Base Tool Plugin."""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.tools.interfaces import ITool
from app.tools.types import ToolHealth, ToolMetadata, ToolStatus


class BaseToolPlugin(ITool):
    """
    Shared base implementation for all concrete tool plugins.

    Provides:
    - Named structured logger
    - OTel async span factory for the tool's run() body
    - A default health_check() that reports healthy unless a subclass
      overrides it with a real connectivity probe (e.g. DatabaseTool
      pinging the engine, HTTPTool checking DNS resolution)
    - ``metadata`` stored as a plain attribute, set once at construction,
      satisfying the ITool.metadata property contract

    Concrete plugins implement only ``run(arguments)`` and, where a real
    health probe is meaningful, override ``health_check()``.
    """

    def __init__(self, metadata: ToolMetadata) -> None:
        self._metadata = metadata
        self._log = get_logger(self.__class__.__module__)

    @property
    def metadata(self) -> ToolMetadata:
        return self._metadata

    def _span(self, operation: str, **attrs: str):
        return create_async_span(
            f"tools.plugin.{self._metadata.name}.{operation}",
            attributes={"tool.name": self._metadata.name, **attrs},
        )

    async def health_check(self) -> ToolHealth:
        """
        Default health check — reports healthy with no external probe.

        Subclasses with a meaningful connectivity target (database,
        HTTP endpoint) should override this with a real check.
        """
        return ToolHealth(
            tool_name=self._metadata.name,
            is_healthy=True,
            status=ToolStatus.COMPLETE,
            checked_at=datetime.now(tz=timezone.utc),
        )