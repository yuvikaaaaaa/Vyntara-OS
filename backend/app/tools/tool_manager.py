"""IOS Tools — Tool Manager."""
from __future__ import annotations

from app.tools.base import BaseToolComponent
from app.tools.exceptions import NoToolAvailableError, ToolError, ToolNotFoundError
from app.tools.interfaces import (
    IToolExecutor,
    IToolManager,
    IToolRegistry,
    IToolSelector,
    IToolValidator,
)
from app.tools.tool_result import ToolResult
from app.tools.types import ToolCapability, ToolRequest, ToolResponse


class ToolManager(BaseToolComponent, IToolManager):
    """
    The single orchestration entry point for the Tool Execution Framework.

    Coordinates:
      - IToolRegistry   — tool discovery (by explicit name or capability)
      - IToolSelector   — best-candidate selection when multiple tools match
      - IToolValidator  — pre-execution schema/permission/policy validation
      - IToolExecutor    — sandboxed, retried, timed execution
      - ToolResult       — output normalization into the final ToolResponse

    Every dependency is injected via the constructor; ToolManager never
    instantiates a component itself, never accesses another component's
    private attributes, and communicates exclusively through the
    interfaces declared in app.tools.interfaces (plus the concrete
    ToolResult normalizer, which has no separate interface abstraction
    defined for this module and is used as-is, consistent with how
    AgentContextBuilder is used directly in the Agent Engine's
    AgentManager).
    """

    def __init__(
        self,
        registry: IToolRegistry,
        selector: IToolSelector,
        validator: IToolValidator,
        executor: IToolExecutor,
        result_normalizer: ToolResult,
    ) -> None:
        super().__init__()
        self._registry = registry
        self._selector = selector
        self._validator = validator
        self._executor = executor
        self._result_normalizer = result_normalizer

    async def invoke(self, request: ToolRequest) -> ToolResponse:
        """
        Execute the complete tool invocation pipeline for a single request:
          1. Resolve candidate tool(s) via the registry (by exact name,
             falling back to capability-based lookup if the named tool
             is not found but request.metadata declares required
             capabilities).
          2. Select the best candidate via IToolSelector.
          3. Validate the request against the selected tool via
             IToolValidator.
          4. Execute via IToolExecutor.
          5. Normalize the result via ToolResult.

        Never raises for expected failure modes (tool not found, no
        candidate available, validation failure, execution failure) —
        all are converted into a well-formed ToolResponse via
        ToolResult.from_exception() so callers always receive a uniform
        response shape. Only truly unexpected errors propagate.
        """
        async with self._span("invoke", tool_name=request.tool_name):
            start_ms = self._now_ms()
            try:
                candidates = self._resolve_candidates(request)
                if not candidates:
                    raise NoToolAvailableError(
                        f"No registered tool matches '{request.tool_name}'.",
                        details={"tool_name": request.tool_name},
                    )

                tool = await self._selector.select(request, candidates)
                await self._validator.validate(request, tool)

                execution = await self._executor.execute(request, tool)
                response = self._result_normalizer.normalize(execution)

                self._log.info(
                    "tool_invocation_complete",
                    tool_name=tool.metadata.name,
                    request_id=request.id,
                    success=response.success,
                    duration_ms=response.duration_ms,
                )
                return response

            except ToolError as exc:
                self._log.warning(
                    "tool_invocation_failed",
                    tool_name=request.tool_name,
                    request_id=request.id,
                    error_code=exc.code,
                    exc=str(exc),
                )
                return self._result_normalizer.from_exception(
                    request.id,
                    request.tool_name,
                    exc,
                    duration_ms=self._elapsed_ms(start_ms),
                )

            except Exception as exc:
                self._log.error(
                    "tool_invocation_unexpected_error",
                    tool_name=request.tool_name,
                    request_id=request.id,
                    exc=str(exc),
                )
                return self._result_normalizer.from_exception(
                    request.id,
                    request.tool_name,
                    exc,
                    duration_ms=self._elapsed_ms(start_ms),
                )

    async def invoke_batch(self, requests: list[ToolRequest]) -> list[ToolResponse]:
        """
        Convenience method: invoke multiple independent tool requests
        concurrently, preserving input order in the returned list.
        """
        import asyncio

        async with self._span("invoke_batch", count=str(len(requests))):
            if not requests:
                return []
            results = await asyncio.gather(*(self.invoke(r) for r in requests))
            return list(results)

    def cancel(self, request_id: str):
        """
        Best-effort cancellation pass-through to the executor, if the
        injected IToolExecutor exposes a cancel() method (not part of
        the minimal IToolExecutor contract, but supported by the
        framework's default ToolExecutor implementation).
        """
        cancel_fn = getattr(self._executor, "cancel", None)
        if cancel_fn is None:
            return None
        return cancel_fn(request_id)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_candidates(self, request: ToolRequest):
        exact = self._registry.get(request.tool_name)
        if exact is not None:
            return [exact]

        required_capabilities = request.metadata.get("required_capabilities")
        if required_capabilities:
            capabilities = [
                ToolCapability(c) if not isinstance(c, ToolCapability) else c
                for c in required_capabilities
            ]
            matches: list = []
            for capability in capabilities:
                matches.extend(self._registry.find_by_capability(capability))
            # Deduplicate while preserving order
            seen: set[str] = set()
            unique = []
            for tool in matches:
                if tool.metadata.name not in seen:
                    seen.add(tool.metadata.name)
                    unique.append(tool)
            return unique

        return []