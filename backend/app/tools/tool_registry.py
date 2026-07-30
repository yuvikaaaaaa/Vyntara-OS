"""IOS Tools — Tool Registry."""
from __future__ import annotations

from datetime import datetime, timezone

from app.tools.base import BaseToolComponent
from app.tools.exceptions import ToolAlreadyRegisteredError, ToolNotFoundError
from app.tools.interfaces import ITool, IToolRegistry
from app.tools.types import ToolCapability, ToolHealth


class ToolRegistry(BaseToolComponent, IToolRegistry):
    """
    In-memory registry of all available tool instances.

    Maintains:
    - tool_name -> ITool instance mapping
    - capability -> set of tool_names index for O(1) capability lookup
    - tool_name -> enabled flag (globally disable a tool without unregistering it)
    - tool_name -> last-known ToolHealth snapshot

    Concrete tool plugins register themselves (or are registered by
    application startup code) via register(); this framework module
    contains no concrete tool implementations itself.
    """

    def __init__(self) -> None:
        super().__init__()
        self._tools: dict[str, ITool] = {}
        self._capability_index: dict[ToolCapability, set[str]] = {}
        self._enabled: dict[str, bool] = {}
        self._health: dict[str, ToolHealth] = {}

    def register(self, tool: ITool) -> None:
        name = tool.metadata.name
        if name in self._tools:
            raise ToolAlreadyRegisteredError(
                f"Tool '{name}' is already registered.", details={"tool_name": name}
            )
        self._tools[name] = tool
        self._enabled[name] = True
        for capability in tool.metadata.capabilities:
            self._capability_index.setdefault(capability, set()).add(name)

        self._log.info(
            "tool_registered",
            tool_name=name,
            tool_type=tool.metadata.tool_type.value,
            capabilities=[c.value for c in tool.metadata.capabilities],
        )

    def unregister(self, tool_name: str) -> None:
        tool = self._tools.pop(tool_name, None)
        self._enabled.pop(tool_name, None)
        self._health.pop(tool_name, None)
        if tool:
            for capability in tool.metadata.capabilities:
                self._capability_index.get(capability, set()).discard(tool_name)
        self._log.info("tool_unregistered", tool_name=tool_name)

    def get(self, tool_name: str) -> ITool | None:
        return self._tools.get(tool_name)

    def get_or_raise(self, tool_name: str) -> ITool:
        tool = self._tools.get(tool_name)
        if tool is None:
            raise ToolNotFoundError(f"Tool '{tool_name}' not found.")
        return tool

    def find_by_capability(self, capability: ToolCapability) -> list[ITool]:
        names = self._capability_index.get(capability, set())
        return [self._tools[n] for n in names if n in self._tools and self._enabled.get(n, False)]

    def find_by_capabilities(self, capabilities: list[ToolCapability]) -> list[ITool]:
        """Return enabled tools satisfying ALL of the given capabilities."""
        if not capabilities:
            return self.list_enabled()
        candidate_sets = [self._capability_index.get(c, set()) for c in capabilities]
        common = set.intersection(*candidate_sets) if candidate_sets else set()
        return [self._tools[n] for n in common if n in self._tools and self._enabled.get(n, False)]

    def list_all(self) -> list[ITool]:
        return list(self._tools.values())

    def list_enabled(self) -> list[ITool]:
        return [t for name, t in self._tools.items() if self._enabled.get(name, False)]

    def is_enabled(self, tool_name: str) -> bool:
        return self._enabled.get(tool_name, False)

    def set_enabled(self, tool_name: str, enabled: bool) -> None:
        if tool_name not in self._tools:
            raise ToolNotFoundError(f"Tool '{tool_name}' not found.")
        self._enabled[tool_name] = enabled
        self._log.info("tool_enabled_changed", tool_name=tool_name, enabled=enabled)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def record_health(self, health: ToolHealth) -> None:
        self._health[health.tool_name] = health

    def get_health(self, tool_name: str) -> ToolHealth | None:
        return self._health.get(tool_name)

    def list_unhealthy(self) -> list[str]:
        return [name for name, h in self._health.items() if not h.is_healthy]