"""IOS — Tool Service."""
from __future__ import annotations

from uuid import UUID

from app.core.enums import ToolName, ToolStatus
from app.core.exceptions import NotFoundError, ToolPermissionDeniedError
from app.models.tool import Tool, ToolExecution, ToolResult
from app.schemas.tool import ToolCreate, ToolRead, ToolUpdate
from app.services.base import BaseService


class ToolService(BaseService):
    """Manages the tool registry and tracks tool execution lifecycle."""

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    async def register_tool(self, data: ToolCreate) -> Tool:
        async with self._span("register_tool"):
            async with self._transaction() as uow:
                existing = await uow.tools.get_by_name(data.tool_name)
                if existing:
                    raise NotFoundError(f"Tool '{data.tool_name}' already registered.")
                tool = Tool(
                    tool_name=data.tool_name,
                    display_name=data.display_name,
                    description=data.description,
                    required_permission=data.required_permission,
                    allowed_roles=data.allowed_roles,
                    input_schema_json=data.input_schema_json,
                    output_schema_json=data.output_schema_json,
                    default_timeout_seconds=data.default_timeout_seconds,
                    max_timeout_seconds=data.max_timeout_seconds,
                    max_retries=data.max_retries,
                    sandbox_memory_mb=data.sandbox_memory_mb,
                    sandbox_cpu_seconds=data.sandbox_cpu_seconds,
                    sandbox_network_allowed=data.sandbox_network_allowed,
                    extra_config=data.extra_config,
                )
                await uow.tools.create(tool)
                self._log.info("tool_registered", tool=data.tool_name)
                return tool

    async def get_tool(self, tool_id: UUID) -> Tool:
        async with self._transaction() as uow:
            tool = await uow.tools.get_by_id(tool_id)
            if not tool or tool.is_deleted:
                raise NotFoundError("Tool not found.")
            return tool

    async def get_tool_by_name(self, name: ToolName) -> Tool:
        async with self._transaction() as uow:
            tool = await uow.tools.get_by_name(name)
            if not tool or tool.is_deleted:
                raise NotFoundError(f"Tool '{name}' not found.")
            return tool

    async def list_tools(self, *, enabled_only: bool = True) -> list[Tool]:
        async with self._transaction() as uow:
            if enabled_only:
                return await uow.tools.list_enabled()
            return await uow.tools.get_all(order_by=Tool.tool_name)

    async def update_tool(self, tool_id: UUID, data: ToolUpdate) -> Tool:
        async with self._transaction() as uow:
            tool = await uow.tools.get_by_id(tool_id, raise_not_found=True)
            await uow.tools.update(tool, data.model_dump(exclude_none=True))
            return tool

    # ------------------------------------------------------------------
    # Permission checking
    # ------------------------------------------------------------------

    def check_permission(
        self, tool: Tool, user_permissions: list[str], user_role: str
    ) -> None:
        """Raise ToolPermissionDeniedError if the user lacks required permission."""
        if not tool.is_enabled:
            raise ToolPermissionDeniedError(
                f"Tool '{tool.tool_name}' is currently disabled."
            )
        if tool.required_permission not in user_permissions:
            if user_role not in tool.allowed_roles:
                raise ToolPermissionDeniedError(
                    f"Permission '{tool.required_permission}' required to use '{tool.tool_name}'."
                )

    # ------------------------------------------------------------------
    # Execution lifecycle
    # ------------------------------------------------------------------

    async def create_execution(
        self,
        task_id: UUID,
        agent_execution_id: UUID,
        tool_name: ToolName,
        input_args: dict,
        *,
        tool_id: UUID | None = None,
        timeout_seconds: int = 60,
        max_attempts: int = 3,
        tool_call_id: str | None = None,
    ) -> ToolExecution:
        async with self._span("create_execution", tool=str(tool_name)):
            execution = ToolExecution(
                task_id=task_id,
                agent_execution_id=agent_execution_id,
                tool_id=tool_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                input_args=input_args,
                timeout_seconds=timeout_seconds,
                max_attempts=max_attempts,
                status=ToolStatus.PENDING,
                permission_checked=True,
                permission_granted=True,
            )
            async with self._transaction() as uow:
                return await uow.tools.create_execution(execution)

    async def mark_running(self, execution_id: UUID) -> None:
        async with self._transaction() as uow:
            await uow.tools.update_execution_status(execution_id, ToolStatus.RUNNING)

    async def mark_complete(
        self,
        execution_id: UUID,
        result_data: dict,
        *,
        duration_ms: int | None = None,
        memory_mb: float | None = None,
        cpu_seconds: float | None = None,
    ) -> ToolResult:
        async with self._span("mark_complete", execution_id=str(execution_id)):
            async with self._transaction() as uow:
                await uow.tools.update_execution_status(
                    execution_id,
                    ToolStatus.COMPLETE,
                    extra={
                        "duration_ms": duration_ms,
                        "sandbox_memory_used_mb": memory_mb,
                        "sandbox_cpu_used_seconds": cpu_seconds,
                    },
                )
                ex = await uow.tools.get_execution_by_id(execution_id)
                if ex and ex.tool_id:
                    await uow.tools.update_stats(
                        ex.tool_id,
                        success=True,
                        latency_ms=duration_ms or 0,
                    )
                # Determine tool_name for denormalisation
                tool_name = ex.tool_name if ex else ToolName.PYTHON_EXECUTOR
                result = ToolResult(
                    execution_id=execution_id,
                    tool_name=tool_name,
                    success=True,
                    **result_data,
                )
                return await uow.tools.create_result(result)

    async def mark_failed(
        self,
        execution_id: UUID,
        error_message: str,
        error_code: str | None = None,
        *,
        traceback: str | None = None,
        sandbox_violation: bool = False,
        timed_out: bool = False,
    ) -> None:
        async with self._span("mark_failed", execution_id=str(execution_id)):
            status = ToolStatus.TIMED_OUT if timed_out else ToolStatus.FAILED
            async with self._transaction() as uow:
                await uow.tools.update_execution_status(
                    execution_id,
                    status,
                    extra={
                        "error_message": error_message,
                        "error_code": error_code,
                        "error_traceback": traceback,
                        "sandbox_violation": sandbox_violation,
                        "timed_out": timed_out,
                    },
                )
                ex = await uow.tools.get_execution_by_id(execution_id)
                if ex and ex.tool_id:
                    await uow.tools.update_stats(
                        ex.tool_id,
                        success=False,
                        latency_ms=ex.duration_ms or 0,
                    )

    async def get_execution(self, execution_id: UUID) -> ToolExecution:
        async with self._transaction() as uow:
            ex = await uow.tools.get_execution_by_id(execution_id)
            if not ex:
                raise NotFoundError("Tool execution not found.")
            return ex

    async def get_result(self, execution_id: UUID) -> ToolResult | None:
        async with self._transaction() as uow:
            return await uow.tools.get_result_for_execution(execution_id)

    async def list_task_executions(
        self,
        task_id: UUID,
        *,
        tool_name: ToolName | None = None,
        status: ToolStatus | None = None,
    ) -> list[ToolExecution]:
        async with self._transaction() as uow:
            return await uow.tools.list_executions_for_task(
                task_id, tool_name=tool_name, status=status
            )

    async def get_sandbox_violations(self, task_id: UUID) -> list[ToolExecution]:
        async with self._transaction() as uow:
            return await uow.tools.get_sandbox_violations(task_id)