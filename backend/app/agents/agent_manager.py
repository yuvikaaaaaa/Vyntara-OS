"""IOS Agents — Agent Manager."""
from __future__ import annotations

from uuid import UUID

from app.agents.agent_context import AgentContextBuilder
from app.agents.base import BaseAgentComponent
from app.agents.exceptions import AgentError
from app.agents.interfaces import (
    IAgentCoordinator,
    IAgentRegistry,
    IAgentSelector,
    IExecutionMonitor,
    IMessageBus,
    ITaskDispatcher,
)
from app.agents.types import AgentHealth, AgentResult, AgentTask, AgentMetrics


class AgentManager(BaseAgentComponent):
    """
    The single orchestration entry point for the Agent Engine.

    Coordinates:
      - IAgentRegistry       — agent discovery and health/state tracking
      - IAgentSelector       — capability/workload-aware agent choice
      - AgentContextBuilder  — merges planner/memory/retrieval/user context
      - ITaskDispatcher      — per-batch sequential/parallel task dispatch
      - IAgentCoordinator    — cross-batch synchronization and aggregation
      - IMessageBus          — inter-agent communication
      - IExecutionMonitor    — progress, history, and metrics tracking

    Every dependency is injected via the constructor; AgentManager never
    instantiates a component itself, never accesses another component's
    private attributes, and communicates exclusively through the
    interfaces declared in app.agents.interfaces (plus the concrete
    AgentContextBuilder, which has no corresponding interface abstraction
    defined for this module and is used as-is).
    """

    def __init__(
        self,
        registry: IAgentRegistry,
        selector: IAgentSelector,
        context_builder: AgentContextBuilder,
        dispatcher: ITaskDispatcher,
        coordinator: IAgentCoordinator,
        message_bus: IMessageBus,
        monitor: IExecutionMonitor,
    ) -> None:
        super().__init__()
        self._registry = registry
        self._selector = selector
        self._context_builder = context_builder
        self._dispatcher = dispatcher
        self._coordinator = coordinator
        self._message_bus = message_bus
        self._monitor = monitor

    # ------------------------------------------------------------------
    # Primary orchestration API
    # ------------------------------------------------------------------

    async def run_plan(
        self,
        batches: list[list[AgentTask]],
        *,
        user_id: UUID | None = None,
        conversation_id: UUID | None = None,
        plan_metadata: dict | None = None,
        user_preferences: dict | None = None,
    ) -> list[AgentResult]:
        """
        Execute a full dependency-ordered set of task batches (typically
        produced by the Planning Engine's ExecutionPlanner.build_steps(),
        grouped by batch_index) and return the aggregated results.

        Args:
            batches: Ordered list of task batches; tasks within a batch
                     may run in parallel, batches run sequentially.
            user_id: Owning user, threaded into every task's execution context.
            conversation_id: Active conversation, for memory/context scoping.
            plan_metadata: Arbitrary metadata from the originating ExecutionPlan.
            user_preferences: User preference dict for personalised execution.

        Returns:
            Aggregated AgentResult list across all batches.

        Raises:
            AgentError: Propagated from AgentCoordinator on unrecoverable
                        batch failure.
        """
        async with self._span("run_plan", batches=str(len(batches))):
            total_tasks = sum(len(b) for b in batches)
            self._log.info(
                "plan_execution_starting",
                batches=len(batches),
                total_tasks=total_tasks,
                user_id=str(user_id) if user_id else None,
            )

            base_context = await self._build_base_context(
                user_id=user_id,
                conversation_id=conversation_id,
                plan_metadata=plan_metadata or {},
                user_preferences=user_preferences or {},
            )

            try:
                results = await self._coordinator.coordinate(batches, base_context)
            except AgentError as exc:
                self._log.error("plan_execution_failed", exc=str(exc))
                raise

            self._log.info(
                "plan_execution_complete",
                total_tasks=len(results),
                succeeded=sum(1 for r in results if r.success),
                failed=sum(1 for r in results if not r.success),
            )
            return results

    async def run_single_task(
        self,
        task: AgentTask,
        *,
        user_id: UUID | None = None,
        conversation_id: UUID | None = None,
    ) -> AgentResult:
        """
        Convenience entry point for executing a single standalone task
        outside of a full multi-batch plan (e.g. an ad-hoc tool
        invocation or a one-off agent call).
        """
        async with self._span("run_single_task", task_id=task.id):
            context = await self._context_builder.build(
                task,
                user_id=user_id,
                conversation_id=conversation_id,
                query_for_retrieval=task.description,
            )
            results = await self._dispatcher.dispatch([task], context.as_dict())
            return results[0] if results else AgentResult(
                task_id=task.id, agent_id="", success=False, error="No result produced."
            )

    # ------------------------------------------------------------------
    # Health & diagnostics
    # ------------------------------------------------------------------

    async def check_all_agents_health(self) -> list[AgentHealth]:
        """Probe every registered agent and record results in the monitor."""
        async with self._span("check_all_agents_health"):
            from app.agents.agent_registry import AgentRegistry
            agents = (
                self._registry.list_all()
                if isinstance(self._registry, AgentRegistry)
                else []
            )
            results: list[AgentHealth] = []
            for agent in agents:
                try:
                    health = await agent.health_check()
                except Exception as exc:
                    from app.agents.types import AgentStatus
                    health = AgentHealth(
                        agent_id=agent.agent_id,
                        is_healthy=False,
                        status=AgentStatus.OFFLINE,
                        error=str(exc),
                    )
                results.append(health)
                self._record_health(health)
            return results

    def get_agent_metrics(self, agent_id: str) -> AgentMetrics | None:
        return self._get_monitor_metrics(agent_id)

    def get_execution_history(self, task_id: str):
        return self._monitor.get_history(task_id)

    def get_active_executions(self):
        return self._monitor.get_active()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _build_base_context(
        self,
        *,
        user_id: UUID | None,
        conversation_id: UUID | None,
        plan_metadata: dict,
        user_preferences: dict,
    ) -> dict:
        """
        Build a minimal shared context dict passed as the baseline for
        every batch dispatched by AgentCoordinator. Per-task enrichment
        (memory/retrieval context specific to that task's description)
        happens inside AgentContextBuilder when individual agents are
        invoked by the dispatcher/executor, not here.
        """
        return {
            "user_id": str(user_id) if user_id else None,
            "conversation_id": str(conversation_id) if conversation_id else None,
            "plan_metadata": plan_metadata,
            "user_preferences": user_preferences,
        }

    def _record_health(self, health: AgentHealth) -> None:
        from app.agents.execution_monitor import ExecutionMonitor
        if isinstance(self._monitor, ExecutionMonitor):
            self._monitor.record_health(health)

    def _get_monitor_metrics(self, agent_id: str) -> AgentMetrics | None:
        from app.agents.execution_monitor import ExecutionMonitor
        if isinstance(self._monitor, ExecutionMonitor):
            return self._monitor.get_agent_metrics(agent_id)
        return None