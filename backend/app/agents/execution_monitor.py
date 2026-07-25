"""IOS Agents — Execution Monitor."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from app.agents.base import BaseAgentComponent
from app.agents.interfaces import IExecutionMonitor
from app.agents.types import AgentExecution, AgentHealth, AgentMetrics, ExecutionStatus


class ExecutionMonitor(BaseAgentComponent, IExecutionMonitor):
    """
    Tracks the full lifecycle of agent executions across a session/task run.

    Maintains:
    - Per-task execution history (all attempts, including retries)
    - Currently active (RUNNING) executions
    - Per-agent aggregate AgentMetrics (success rate, avg latency, avg tokens)
    - Recent AgentHealth snapshots reported by agents

    Purely an in-memory observer — never mutates or drives execution
    itself; AgentExecutor and TaskDispatcher call record_start/
    record_completion as executions progress.
    """

    def __init__(self, *, max_history_per_task: int = 20) -> None:
        super().__init__()
        self._history: dict[str, list[AgentExecution]] = defaultdict(list)
        self._active: dict[str, AgentExecution] = {}
        self._agent_metrics: dict[str, AgentMetrics] = {}
        self._health_snapshots: dict[str, AgentHealth] = {}
        self._max_history = max_history_per_task

    def record_start(self, execution: AgentExecution) -> None:
        self._active[execution.id] = execution
        self._log.debug(
            "execution_started",
            execution_id=execution.id,
            agent_id=execution.agent_id,
            task_id=execution.task.id,
        )

    def record_completion(self, execution: AgentExecution) -> None:
        self._active.pop(execution.id, None)

        history = self._history[execution.task.id]
        history.append(execution)
        if len(history) > self._max_history:
            del history[: len(history) - self._max_history]

        self._update_agent_metrics(execution)

        log_fn = self._log.info if execution.status == ExecutionStatus.COMPLETE else self._log.warning
        log_fn(
            "execution_recorded",
            execution_id=execution.id,
            agent_id=execution.agent_id,
            task_id=execution.task.id,
            status=execution.status.value,
            duration_ms=execution.duration_ms,
        )

    def get_history(self, task_id: str) -> list[AgentExecution]:
        return list(self._history.get(task_id, []))

    def get_active(self) -> list[AgentExecution]:
        return list(self._active.values())

    # ------------------------------------------------------------------
    # Health tracking
    # ------------------------------------------------------------------

    def record_health(self, health: AgentHealth) -> None:
        self._health_snapshots[health.agent_id] = health

    def get_health(self, agent_id: str) -> AgentHealth | None:
        return self._health_snapshots.get(agent_id)

    def list_unhealthy_agents(self) -> list[str]:
        return [
            aid for aid, h in self._health_snapshots.items() if not h.is_healthy
        ]

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def get_agent_metrics(self, agent_id: str) -> AgentMetrics | None:
        return self._agent_metrics.get(agent_id)

    def all_agent_metrics(self) -> list[AgentMetrics]:
        return list(self._agent_metrics.values())

    def get_retry_count(self, task_id: str) -> int:
        history = self._history.get(task_id, [])
        return max(0, len(history) - 1)

    def get_failure_rate(self, agent_id: str) -> float:
        metrics = self._agent_metrics.get(agent_id)
        if metrics is None or metrics.total_executions == 0:
            return 0.0
        return metrics.failure_count / metrics.total_executions

    def summary(self) -> dict:
        """Aggregate snapshot suitable for a monitoring dashboard."""
        return {
            "active_executions": len(self._active),
            "tracked_tasks": len(self._history),
            "agents_tracked": len(self._agent_metrics),
            "unhealthy_agents": self.list_unhealthy_agents(),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_agent_metrics(self, execution: AgentExecution) -> None:
        agent_id = execution.agent_id
        if not agent_id:
            return
        metrics = self._agent_metrics.setdefault(
            agent_id, AgentMetrics(agent_id=agent_id)
        )
        metrics.total_executions += 1
        if execution.status == ExecutionStatus.COMPLETE:
            metrics.success_count += 1
        else:
            metrics.failure_count += 1

        if execution.duration_ms is not None:
            alpha = 0.2
            metrics.avg_latency_ms = (
                execution.duration_ms
                if metrics.total_executions == 1
                else metrics.avg_latency_ms * (1 - alpha) + execution.duration_ms * alpha
            )

        if execution.result and execution.result.tokens_used is not None:
            alpha = 0.2
            tokens = execution.result.tokens_used
            metrics.avg_tokens_used = (
                tokens
                if metrics.total_executions == 1
                else metrics.avg_tokens_used * (1 - alpha) + tokens * alpha
            )

        metrics.current_workload = len(
            [e for e in self._active.values() if e.agent_id == agent_id]
        )