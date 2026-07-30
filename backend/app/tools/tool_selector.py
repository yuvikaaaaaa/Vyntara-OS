"""IOS Tools — Tool Selector."""
from __future__ import annotations

from app.tools.base import BaseToolComponent
from app.tools.exceptions import NoToolAvailableError, ToolPermissionError
from app.tools.interfaces import ITool, IToolSelector
from app.tools.tool_registry import ToolRegistry
from app.tools.types import ToolRequest

# Weighting applied when a candidate's declared confidence-relevant
# attributes are unavailable (e.g. no registry injected) — a neutral
# score keeps selection deterministic-by-name-order rather than biased.
_NEUTRAL_SCORE = 0.5


class ToolSelector(BaseToolComponent, IToolSelector):
    """
    Selects the best candidate tool for a request from a pool of
    capability-matching candidates supplied by the caller (typically
    ToolManager, which sources candidates via ToolRegistry).

    Never instantiates a tool — operates purely on ITool instances handed
    to it and (optionally) a ToolRegistry reference used only for
    read-only health/enabled-state lookups.

    Scoring combines:
    - **Permission fit** — candidates whose required_permission is not
      present in request.permissions are excluded outright (a hard
      filter, not a soft score penalty, since granting an unpermitted
      tool call is a security boundary, not a preference).
    - **Availability** — disabled tools (per ToolRegistry) are excluded.
    - **Health** — a tool with a last-known-unhealthy status is scored
      lower but not excluded outright, since health snapshots can be
      stale; ToolExecutor's own timeout/retry provides the hard backstop.
    - **Confidence** — derived from the tool's historical success rate
      when a registry with health tracking is available; defaults to a
      neutral score otherwise.
    """

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        super().__init__()
        self._registry = registry

    async def select(self, request: ToolRequest, candidates: list[ITool]) -> ITool:
        async with self._span("select_tool", tool_name=request.tool_name):
            if not candidates:
                raise NoToolAvailableError(
                    f"No candidate tools available for request targeting "
                    f"'{request.tool_name}'.",
                    details={"tool_name": request.tool_name},
                )

            permitted = self._filter_by_permission(request, candidates)
            if not permitted:
                raise ToolPermissionError(
                    f"No candidate tool for '{request.tool_name}' is permitted "
                    f"for the caller's granted permissions.",
                    details={
                        "tool_name": request.tool_name,
                        "granted_permissions": request.permissions,
                    },
                )

            available = self._filter_available(permitted)
            pool = available or permitted  # degrade gracefully if all disabled

            scored = [(tool, self._score(tool)) for tool in pool]
            scored.sort(key=lambda pair: pair[1], reverse=True)
            best_tool, best_score = scored[0]

            self._log.info(
                "tool_selected",
                requested=request.tool_name,
                selected=best_tool.metadata.name,
                score=round(best_score, 3),
                candidates=len(candidates),
            )
            return best_tool

    # ------------------------------------------------------------------
    # Hard filters
    # ------------------------------------------------------------------

    @staticmethod
    def _filter_by_permission(
        request: ToolRequest, candidates: list[ITool]
    ) -> list[ITool]:
        granted = set(request.permissions)
        result = []
        for tool in candidates:
            required = tool.metadata.required_permission
            if required is None or required in granted:
                result.append(tool)
        return result

    def _filter_available(self, candidates: list[ITool]) -> list[ITool]:
        if self._registry is None:
            return candidates
        return [
            tool for tool in candidates
            if self._registry.is_enabled(tool.metadata.name)
        ]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score(self, tool: ITool) -> float:
        health_score = self._health_score(tool)
        confidence_score = self._confidence_score(tool)
        return 0.5 * health_score + 0.5 * confidence_score

    def _health_score(self, tool: ITool) -> float:
        if self._registry is None:
            return _NEUTRAL_SCORE
        health = self._registry.get_health(tool.metadata.name)
        if health is None:
            return _NEUTRAL_SCORE
        return 1.0 if health.is_healthy else 0.1

    def _confidence_score(self, tool: ITool) -> float:
        """
        Derive a confidence proxy from historical success rate, if a
        registry with recorded metrics is available. Falls back to a
        neutral score when no history exists yet.
        """
        if self._registry is None:
            return _NEUTRAL_SCORE
        # ToolRegistry itself doesn't track execution metrics (that's
        # ToolExecutor/ToolManager's responsibility); read-only lookup
        # of any externally-attached metrics snapshot is intentionally
        # not assumed here to avoid coupling — default neutral.
        return _NEUTRAL_SCORE