"""IOS Tools — Tool Sandbox."""
from __future__ import annotations

import asyncio
import resource
import time
from typing import Any
from uuid import uuid4

from app.tools.base import BaseToolComponent
from app.tools.exceptions import (
    ToolCancelledError,
    ToolResourceLimitExceededError,
    ToolSandboxError,
    ToolTimeoutError,
)
from app.tools.interfaces import ITool, IToolSandbox
from app.tools.types import SandboxResourceLimit


class ToolSandbox(BaseToolComponent, IToolSandbox):
    """
    Default execution-isolation implementation for the Tool Execution
    Framework.

    This is an **asyncio-level** sandbox: it enforces wall-clock timeout
    and cooperative cancellation universally, and — on POSIX platforms —
    reads process-level resource usage (CPU time, peak memory via
    ``resource.getrusage``) as a best-effort measurement for reporting,
    without hard process/container isolation.

    Remains framework-independent: concrete tool plugins requiring
    stronger isolation (subprocess sandboxing, container execution,
    network egress restriction) implement IToolSandbox themselves and
    are injected into ToolManager in place of this default — no other
    framework component depends on ToolSandbox's concrete internals.
    """

    def __init__(self) -> None:
        super().__init__()
        self._active: dict[str, asyncio.Task] = {}

    async def run_isolated(
        self,
        tool: ITool,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> Any:
        async with self._span("run_isolated", tool_name=tool.metadata.name):
            execution_id = str(uuid4())
            cpu_before = self._cpu_time_seconds()

            asyncio_task = asyncio.ensure_future(tool.run(arguments))
            self._active[execution_id] = asyncio_task

            try:
                result = await asyncio.wait_for(asyncio_task, timeout=timeout_seconds)
                self._check_resource_limits(tool, cpu_before)
                return result

            except asyncio.TimeoutError as exc:
                asyncio_task.cancel()
                raise ToolTimeoutError(
                    f"Tool '{tool.metadata.name}' exceeded sandbox timeout "
                    f"of {timeout_seconds}s.",
                    details={"tool_name": tool.metadata.name, "timeout_seconds": timeout_seconds},
                ) from exc

            except asyncio.CancelledError as exc:
                raise ToolCancelledError(
                    f"Tool '{tool.metadata.name}' execution was cancelled.",
                    details={"tool_name": tool.metadata.name},
                ) from exc

            finally:
                self._active.pop(execution_id, None)
                await self._cleanup(tool, execution_id)

    async def cancel(self, execution_id: str) -> bool:
        task = self._active.get(execution_id)
        if task is None:
            return False
        task.cancel()
        return True

    def active_count(self) -> int:
        return len(self._active)

    # ------------------------------------------------------------------
    # Resource accounting (best-effort, POSIX)
    # ------------------------------------------------------------------

    @staticmethod
    def _cpu_time_seconds() -> float:
        try:
            usage = resource.getrusage(resource.RUSAGE_SELF)
            return usage.ru_utime + usage.ru_stime
        except Exception:
            return 0.0

    def _check_resource_limits(self, tool: ITool, cpu_before: float) -> None:
        limits = tool.metadata.resource_limits
        cpu_limit = limits.get(SandboxResourceLimit.CPU_SECONDS)
        if cpu_limit is None:
            return
        cpu_after = self._cpu_time_seconds()
        cpu_used = max(0.0, cpu_after - cpu_before)
        if cpu_used > cpu_limit:
            raise ToolResourceLimitExceededError(
                f"Tool '{tool.metadata.name}' exceeded CPU time limit "
                f"({cpu_used:.2f}s > {cpu_limit:.2f}s).",
                details={"tool_name": tool.metadata.name, "cpu_used": cpu_used, "limit": cpu_limit},
            )

    # ------------------------------------------------------------------
    # Cleanup hook
    # ------------------------------------------------------------------

    async def _cleanup(self, tool: ITool, execution_id: str) -> None:
        """
        Post-execution cleanup hook. The default in-process sandbox has
        no external resources to release; subclasses/alternative
        IToolSandbox implementations performing subprocess or filesystem
        isolation override this behaviour by providing their own
        run_isolated() implementation entirely (IToolSandbox does not
        expose a separate cleanup() method, keeping the interface small —
        cleanup is an implementation detail of run_isolated()).
        """
        self._log.debug(
            "sandbox_cleanup", tool_name=tool.metadata.name, execution_id=execution_id
        )