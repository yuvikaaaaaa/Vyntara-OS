"""IOS Agents — Agent Registry."""
from __future__ import annotations

from datetime import datetime, timezone

from app.agents.base import BaseAgentComponent
from app.agents.exceptions import AgentAlreadyRegisteredError, AgentNotFoundError
from app.agents.interfaces import IAgent, IAgentRegistry
from app.agents.types import AgentCapability, AgentState, AgentStatus


class AgentRegistry(BaseAgentComponent, IAgentRegistry):
    """
    In-memory registry of all available agent instances.

    Maintains:
    - agent_id -> IAgent instance mapping
    - agent_id -> AgentState (status, workload, heartbeat)
    - capability -> set of agent_ids index for O(1) capability lookup

    Thread-safety note: this registry assumes single-event-loop asyncio
    usage (no cross-thread mutation), consistent with the rest of the
    codebase's async-first design.
    """

    def __init__(self) -> None:
        super().__init__()
        self._agents: dict[str, IAgent] = {}
        self._states: dict[str, AgentState] = {}
        self._capability_index: dict[AgentCapability, set[str]] = {}

    def register(self, agent: IAgent) -> None:
        if agent.agent_id in self._agents:
            raise AgentAlreadyRegisteredError(
                f"Agent '{agent.agent_id}' is already registered.",
                details={"agent_id": agent.agent_id},
            )
        self._agents[agent.agent_id] = agent
        self._states[agent.agent_id] = AgentState(
            agent_id=agent.agent_id,
            role=self._infer_role(agent),
            status=AgentStatus.IDLE,
            last_heartbeat=datetime.now(tz=timezone.utc),
        )
        for capability in agent.capabilities:
            self._capability_index.setdefault(capability, set()).add(agent.agent_id)

        self._log.info(
            "agent_registered",
            agent_id=agent.agent_id,
            capabilities=[c.value for c in agent.capabilities],
        )

    def unregister(self, agent_id: str) -> None:
        agent = self._agents.pop(agent_id, None)
        self._states.pop(agent_id, None)
        if agent:
            for capability in agent.capabilities:
                self._capability_index.get(capability, set()).discard(agent_id)
        self._log.info("agent_unregistered", agent_id=agent_id)

    def get(self, agent_id: str) -> IAgent | None:
        return self._agents.get(agent_id)

    def get_or_raise(self, agent_id: str) -> IAgent:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise AgentNotFoundError(f"Agent '{agent_id}' not found.")
        return agent

    def find_by_capability(self, capability: AgentCapability) -> list[IAgent]:
        ids = self._capability_index.get(capability, set())
        return [self._agents[aid] for aid in ids if aid in self._agents]

    def find_by_capabilities(
        self, capabilities: list[AgentCapability]
    ) -> list[IAgent]:
        """Return agents satisfying ALL of the given capabilities."""
        if not capabilities:
            return list(self._agents.values())
        candidate_sets = [
            self._capability_index.get(cap, set()) for cap in capabilities
        ]
        common_ids = set.intersection(*candidate_sets) if candidate_sets else set()
        return [self._agents[aid] for aid in common_ids if aid in self._agents]

    def list_states(self) -> list[AgentState]:
        return list(self._states.values())

    def get_state(self, agent_id: str) -> AgentState | None:
        return self._states.get(agent_id)

    def update_state(self, agent_id: str, state: AgentState) -> None:
        self._states[agent_id] = state

    def mark_busy(self, agent_id: str) -> None:
        state = self._states.get(agent_id)
        if state:
            state.status = AgentStatus.BUSY
            state.active_task_count += 1

    def mark_idle(self, agent_id: str) -> None:
        state = self._states.get(agent_id)
        if state:
            state.active_task_count = max(0, state.active_task_count - 1)
            if state.active_task_count == 0:
                state.status = AgentStatus.IDLE

    def heartbeat(self, agent_id: str) -> None:
        state = self._states.get(agent_id)
        if state:
            state.last_heartbeat = datetime.now(tz=timezone.utc)

    def list_all(self) -> list[IAgent]:
        return list(self._agents.values())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_role(agent: IAgent):
        from app.agents.types import AgentRole
        role_value = getattr(agent, "role", None)
        if isinstance(role_value, AgentRole):
            return role_value
        return AgentRole.GENERIC