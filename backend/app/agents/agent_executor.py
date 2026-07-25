"""IOS Agents — Agent Executor."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.agents.base import BaseAgentComponent
from app.agents.exceptions import AgentCancelledError, AgentExecutionError, AgentTimeoutError
from app.agents.interfaces import IAgent, IAgentExecutor
from app.agents.types import AgentExecution, AgentResult, AgentTask, ExecutionStatus


class AgentExecutor(BaseAgentComponent, IAgentExecutor):
    """
    Executes a single agent against a single task.

    Responsibilities:
    - Wrap IAgent.execute() with an enforced timeout (task.timeout_seconds)
    - Support cooperative cancellation via asyncio.Task.cancel()
    - Produce a fully-populated AgentExecution record (timing, status,
      result/error) for every invocation, success or failure
    - Track per-agent execution metrics (success rate, avg latency,
      avg tokens) via the inherited BaseAgentComponent EMA accumulator

    Retries are the responsibility of the caller (TaskDispatcher) — this
    component executes exactly one attempt per call, keeping its
    contract simple and composable.
    """

    def __init__(self) -> None:
        super().__init__()
        self._active: dict[str, asyncio.Task] = {}   # execution.id -> running task

    async def execute(
        self, agent: IAgent, task: AgentTask, context: dict
    ) -> AgentExecution:
        async with self._span("execute_agent", agent_id=agent.agent_id, task_id=task.id):
            execution = AgentExecution(
                task=task,
                agent_id=agent.agent_id,
                status=ExecutionStatus.RUNNING,
                started_at=datetime.now(tz=timezone.utc),
            )
            start_ms = self._now_ms()

            asyncio_task = asyncio.ensure_future(
                self._run_with_timeout(agent, task, context, execution.id)
            )
            self._active[execution.id] = asyncio_task

            try:
                result = await asyncio_task
                execution.status = ExecutionStatus.COMPLETE if result.success else ExecutionStatus.FAILED
                execution.result = result
                self._record_metrics(agent.agent_id, success=result.success, latency_ms=self._elapsed_ms(start_ms), tokens=result.tokens_used)

            except asyncio.CancelledError:
                execution.status = ExecutionStatus.CANCELLED
                execution.error = "Execution was cancelled."
                self._record_metrics(agent.agent_id, success=False, latency_ms=self._elapsed_ms(start_ms), tokens=None)
                self._log.warning("agent_execution_cancelled", agent_id=agent.agent_id, task_id=task.id)

            except AgentTimeoutError as exc:
                execution.status = ExecutionStatus.TIMED_OUT
                execution.error = str(exc)
                execution.result = AgentResult(
                    task_id=task.id, agent_id=agent.agent_id, success=False, error=str(exc)
                )
                self._record_metrics(agent.agent_id, success=False, latency_ms=self._elapsed_ms(start_ms), tokens=None)
                self._log.warning("agent_execution_timeout", agent_id=agent.agent_id, task_id=task.id)

            except Exception as exc:
                execution.status = ExecutionStatus.FAILED
                execution.error = str(exc)
                execution.result = AgentResult(
                    task_id=task.id, agent_id=agent.agent_id, success=False, error=str(exc)
                )
                self._record_metrics(agent.agent_id, success=False, latency_ms=self._elapsed_ms(start_ms), tokens=None)
                self._log.error(
                    "agent_execution_error", agent_id=agent.agent_id, task_id=task.id, exc=str(exc)
                )

            finally:
                self._active.pop(execution.id, None)
                execution.completed_at = datetime.now(tz=timezone.utc)
                execution.duration_ms = self._elapsed_ms(start_ms)

            self._log.info(
                "agent_execution_finished",
                agent_id=agent.agent_id,
                task_id=task.id,
                status=execution.status.value,
                duration_ms=execution.duration_ms,
            )
            return execution

    async def cancel(self, execution_id: str) -> bool:
        """
        Request cancellation of an in-flight execution.

        Returns:
            True if a matching active execution was found and cancelled.

        Raises:
            AgentCancelledError: never raised here — cancellation is
            cooperative and surfaced via the execution's own status field
            once the wrapped coroutine observes the CancelledError.
        """
        task = self._active.get(execution_id)
        if task is None:
            return False
        task.cancel()
        return True

    def active_count(self) -> int:
        return len(self._active)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run_with_timeout(
        self, agent: IAgent, task: AgentTask, context: dict, execution_id: str
    ) -> AgentResult:
        try:
            return await asyncio.wait_for(
                agent.execute(task, context), timeout=task.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            raise AgentTimeoutError(
                f"Agent '{agent.agent_id}' timed out on task '{task.id}' after "
                f"{task.timeout_seconds}s.",
                details={"agent_id": agent.agent_id, "task_id": task.id},
            ) from exc

    def _record_metrics(
        self,
        agent_id: str,
        *,
        success: bool,
        latency_ms: int,
        tokens: int | None,
    ) -> None:
        self._incr(f"{agent_id}.total_executions")
        self._incr(f"{agent_id}.success" if success else f"{agent_id}.failure")
        self._ema(f"{agent_id}.avg_latency_ms", float(latency_ms))
        if tokens is not None:
            self._ema(f"{agent_id}.avg_tokens", float(tokens))