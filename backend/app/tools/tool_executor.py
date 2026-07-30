"""IOS Tools — Tool Executor."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.tools.base import BaseToolComponent
from app.tools.exceptions import ToolCancelledError, ToolExecutionError, ToolTimeoutError
from app.tools.interfaces import IToolExecutor, IToolSandbox, ITool
from app.tools.types import ToolExecution, ToolRequest, ToolResponse, ToolStatus

# Exceptions considered transient and therefore eligible for retry.
_RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = (
    ToolTimeoutError,
    ConnectionError,
    TimeoutError,
)


class ToolExecutor(BaseToolComponent, IToolExecutor):
    """
    Executes a single validated tool request end-to-end.

    Responsibilities:
    - Delegate isolated execution to the injected IToolSandbox — never
      calls ITool.run() directly, always goes through the sandbox
    - Retry transient failures (ToolTimeoutError, connection/timeout
      errors) up to the tool's declared max_retries, with exponential
      backoff via the inherited BaseToolComponent helper
    - Support cooperative cancellation by delegating to
      IToolSandbox.cancel()
    - Produce a fully-populated ToolExecution record (timing, status,
      response/error) for every invocation, success or failure
    - Track per-tool execution metrics (success rate, avg latency) via
      the inherited EMA accumulator

    Never instantiates a tool — operates purely on the ITool instance and
    IToolSandbox handed to it via execute()/constructor respectively.
    """

    def __init__(self, sandbox: IToolSandbox) -> None:
        super().__init__()
        self._sandbox = sandbox
        self._active_execution_ids: dict[str, str] = {}  # request.id -> execution.id

    async def execute(self, request: ToolRequest, tool: ITool) -> ToolExecution:
        async with self._span("execute_tool", tool_name=tool.metadata.name):
            execution = ToolExecution(
                request=request,
                status=ToolStatus.RUNNING,
                started_at=datetime.now(tz=timezone.utc),
            )
            self._active_execution_ids[request.id] = execution.id
            start_ms = self._now_ms()

            timeout = request.timeout_seconds or tool.metadata.default_timeout_seconds
            max_retries = max(1, tool.metadata.max_retries)

            try:
                output = await self._run_with_retry(tool, request, timeout, max_retries)
                execution.status = ToolStatus.COMPLETE
                execution.response = ToolResponse(
                    request_id=request.id,
                    tool_name=tool.metadata.name,
                    success=True,
                    output=output,
                    output_text=self._stringify(output),
                    duration_ms=self._elapsed_ms(start_ms),
                )
                self._record_metrics(tool.metadata.name, success=True, latency_ms=self._elapsed_ms(start_ms))

            except ToolCancelledError as exc:
                execution.status = ToolStatus.CANCELLED
                execution.error = str(exc)
                execution.response = self._error_response(request, tool.metadata.name, exc, start_ms)
                self._record_metrics(tool.metadata.name, success=False, latency_ms=self._elapsed_ms(start_ms))
                self._log.warning("tool_execution_cancelled", tool_name=tool.metadata.name, request_id=request.id)

            except ToolTimeoutError as exc:
                execution.status = ToolStatus.TIMED_OUT
                execution.error = str(exc)
                execution.response = self._error_response(request, tool.metadata.name, exc, start_ms)
                self._record_metrics(tool.metadata.name, success=False, latency_ms=self._elapsed_ms(start_ms))
                self._log.warning("tool_execution_timeout", tool_name=tool.metadata.name, request_id=request.id)

            except Exception as exc:
                execution.status = ToolStatus.FAILED
                execution.error = str(exc)
                execution.response = self._error_response(request, tool.metadata.name, exc, start_ms)
                self._record_metrics(tool.metadata.name, success=False, latency_ms=self._elapsed_ms(start_ms))
                self._log.error(
                    "tool_execution_error", tool_name=tool.metadata.name, request_id=request.id, exc=str(exc)
                )

            finally:
                self._active_execution_ids.pop(request.id, None)
                execution.completed_at = datetime.now(tz=timezone.utc)
                execution.duration_ms = self._elapsed_ms(start_ms)

            self._log.info(
                "tool_execution_finished",
                tool_name=tool.metadata.name,
                request_id=request.id,
                status=execution.status.value,
                duration_ms=execution.duration_ms,
            )
            return execution

    async def cancel(self, request_id: str) -> bool:
        """Request cancellation of an in-flight execution by request id."""
        execution_id = self._active_execution_ids.get(request_id)
        if execution_id is None:
            return False
        return await self._sandbox.cancel(execution_id)

    def active_count(self) -> int:
        return len(self._active_execution_ids)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_with_retry(
        self,
        tool: ITool,
        request: ToolRequest,
        timeout: float,
        max_retries: int,
    ):
        async def _attempt():
            return await self._sandbox.run_isolated(
                tool, request.arguments, timeout_seconds=timeout
            )

        return await self._with_retry(
            _attempt,
            max_retries=max_retries,
            base_delay=1.0,
            retryable_exceptions=_RETRYABLE_EXCEPTIONS,
            label=f"{tool.metadata.name}:{request.id}",
        )

    def _error_response(
        self,
        request: ToolRequest,
        tool_name: str,
        exc: Exception,
        start_ms: int,
    ) -> ToolResponse:
        error_code = getattr(exc, "code", exc.__class__.__name__)
        return ToolResponse(
            request_id=request.id,
            tool_name=tool_name,
            success=False,
            error=str(exc),
            error_code=error_code,
            duration_ms=self._elapsed_ms(start_ms),
        )

    def _record_metrics(self, tool_name: str, *, success: bool, latency_ms: int) -> None:
        self._incr(f"{tool_name}.total_executions")
        self._incr(f"{tool_name}.success" if success else f"{tool_name}.failure")
        self._ema(f"{tool_name}.avg_latency_ms", float(latency_ms))

    @staticmethod
    def _stringify(output) -> str | None:
        if output is None:
            return None
        if isinstance(output, str):
            return output
        try:
            import json
            return json.dumps(output, default=str)
        except Exception:
            return str(output)