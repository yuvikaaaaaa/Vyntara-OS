"""IOS Agents — Agent Selector."""
from __future__ import annotations

from app.agents.agent_registry import AgentRegistry
from app.agents.base import BaseAgentComponent
from app.agents.exceptions import NoAgentAvailableError
from app.agents.interfaces import IAgent, IAgentSelector
from app.agents.types import AgentHealth, AgentStatus, AgentTask


class AgentSelector(BaseAgentComponent, IAgentSelector):
    """
    Selects the best candidate agent for a given task from a pool of
    capability-matching candidates.

    Scoring combines:
    - **Capability coverage** — fraction of task.required_capabilities the
      agent satisfies (agents missing required capabilities are excluded
      entirely upstream by AgentRegistry.find_by_capabilities; this score
      rewards agents with *additional* relevant capabilities).
    - **Workload** — inverse of current active_task_count (registry state);
      prefers less-busy agents to spread load.
    - **Health** — agents reporting AgentStatus.UNHEALTHY or OFFLINE are
      excluded; healthy agents score higher than unknown-health agents.
    - **Priority hint** — task.priority influences how strongly workload
      balancing is weighted vs raw capability match (high-priority tasks
      prefer the most capable agent even if busier).
    """

    def __init__(self, registry: AgentRegistry | None = None) -> None:
        super().__init__()
        self._registry = registry

    async def select(self, task: AgentTask, candidates: list[IAgent]) -> IAgent:
        async with self._span("select_agent", task_id=task.id):
            if not candidates:
                raise NoAgentAvailableError(
                    f"No candidate agents available for task '{task.name}'.",
                    details={"task_id": task.id, "required_capabilities": [
                        c.value for c in task.required_capabilities
                    ]},
                )

            healthy_candidates = self._filter_unhealthy(candidates)
            pool = healthy_candidates or candidates  # degrade gracefully if all unhealthy

            scored = [(agent, self._score(agent, task)) for agent in pool]
            scored.sort(key=lambda pair: pair[1], reverse=True)
            best_agent, best_score = scored[0]

            self._log.info(
                "agent_selected",
                task_id=task.id,
                agent_id=best_agent.agent_id,
                score=round(best_score, 3),
                candidates=len(candidates),
            )
            return best_agent

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score(self, agent: IAgent, task: AgentTask) -> float:
        capability_score = self._capability_score(agent, task)
        workload_score = self._workload_score(agent)
        priority_weight = min(1.0, task.priority / 10.0)

        # High-priority tasks weight capability match more heavily;
        # low-priority tasks weight load-balancing more heavily.
        return (
            capability_score * (0.5 + 0.4 * priority_weight)
            + workload_score * (0.5 - 0.4 * priority_weight)
        )

    def _capability_score(self, agent: IAgent, task: AgentTask) -> float:
        if not task.required_capabilities:
            return 1.0
        required = set(task.required_capabilities)
        agent_caps = set(agent.capabilities)
        matched = required & agent_caps
        extra = agent_caps - required
        base = len(matched) / len(required)
        bonus = min(0.2, 0.02 * len(extra))
        return min(1.0, base + bonus)

    def _workload_score(self, agent: IAgent) -> float:
        if self._registry is None:
            return 0.5
        state = self._registry.get_state(agent.agent_id)
        if state is None:
            return 0.5
        # Inverse relationship: 0 active tasks -> 1.0, saturates toward 0
        return 1.0 / (1.0 + state.active_task_count)

    # ------------------------------------------------------------------
    # Health filtering
    # ------------------------------------------------------------------

    def _filter_unhealthy(self, candidates: list[IAgent]) -> list[IAgent]:
        if self._registry is None:
            return candidates
        healthy: list[IAgent] = []
        for agent in candidates:
            state = self._registry.get_state(agent.agent_id)
            if state is None or state.status not in (AgentStatus.UNHEALTHY, AgentStatus.OFFLINE):
                healthy.append(agent)
        return healthy