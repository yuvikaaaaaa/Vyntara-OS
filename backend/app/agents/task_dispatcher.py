"""IOS Agents — Task Dispatcher."""
from __future__ import annotations

import asyncio

from app.agents.agent_registry import AgentRegistry
from app.agents.agent_selector import AgentSelector
from app.agents.base import BaseAgentComponent
from app.agents.exceptions import MaxRetriesExceededError, TaskDispatchError
from app.agents.interfaces import IAgentExecutor, ITaskDispatcher
from app.agents.types import AgentResult, AgentTask, ExecutionStatus


class TaskDispatcher(BaseAgentComponent, ITaskDispatcher):
    """
    Dispatches a batch of AgentTask objects for execution.

    Execution mode is inferred from batch composition: a batch containing
    more than one task is executed in parallel (asyncio.gather); a
    single-task batch runs sequentially. Tasks are dispatched in priority
    order within a batch. Each task is retried up to its own
    ``max_retries`` via AgentExecutor before being marked failed.

    Communicates only through IAgentRegistry (via AgentRegistry's
    capability lookup), IAgentSelector, and IAgentExecutor — no direct
    coupling to any specific agent implementation.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        selector: AgentSelector,
        executor: IAgentExecutor,
    ) -> None:
        super().__init__()
        self._registry = registry
        self._selector = selector
        self._executor = executor

    async def dispatch(
        self, tasks: list[AgentTask], context: dict
    ) -> list[AgentResult]:
        async with self._span("dispatch_batch", tasks=str(len(tasks))):
            if not tasks:
                return []

            ordered = sorted(tasks, key=lambda t: t.priority, reverse=True)

            if len(ordered) > 1:
                results = await self._dispatch_parallel(ordered, context)
            else:
                results = [await self._dispatch_one(ordered[0], context)]

            self._log.info(
                "batch_dispatched",
                tasks=len(tasks),
                succeeded=sum(1 for r in results if r.success),
                failed=sum(1 for r in results if not r.success),
            )
            return results

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _dispatch_parallel(
        self, tasks: list[AgentTask], context: dict
    ) -> list[AgentResult]:
        coros = [self._dispatch_one(task, context) for task in tasks]
        return list(await asyncio.gather(*coros))

    async def _dispatch_one(self, task: AgentTask, context: dict) -> AgentResult:
        async with self._span("dispatch_task", task_id=task.id):
            try:
                candidates = self._registry.find_by_capabilities(task.required_capabilities)
                agent = await self._selector.select(task, candidates)
            except Exception as exc:
                self._log.error("agent_selection_failed", task_id=task.id, exc=str(exc))
                return AgentResult(
                    task_id=task.id,
                    agent_id="",
                    success=False,
                    error=f"Agent selection failed: {exc}",
                )

            last_result: AgentResult | None = None
            for attempt in range(1, task.max_retries + 1):
                execution = await self._executor.execute(agent, task, context)
                if execution.status == ExecutionStatus.COMPLETE and execution.result:
                    if execution.result.success:
                        return execution.result
                    last_result = execution.result
                elif execution.result:
                    last_result = execution.result

                if attempt < task.max_retries:
                    self._log.warning(
                        "task_dispatch_retry",
                        task_id=task.id,
                        agent_id=agent.agent_id,
                        attempt=attempt,
                    )
                    await asyncio.sleep(min(2 ** attempt, 8))

            return last_result or AgentResult(
                task_id=task.id,
                agent_id=agent.agent_id,
                success=False,
                error=f"Task failed after {task.max_retries} attempt(s) with no result.",
            )