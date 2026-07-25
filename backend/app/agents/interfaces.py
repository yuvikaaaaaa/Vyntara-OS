"""IOS Agents — Interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Callable

from app.agents.types import (
    AgentCapability,
    AgentExecution,
    AgentHealth,
    AgentMessage,
    AgentResult,
    AgentState,
    AgentTask,
)


class IAgent(ABC):
    """Contract every specialized agent implementation must satisfy."""

    @property
    @abstractmethod
    def agent_id(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> list[AgentCapability]: ...

    @abstractmethod
    async def execute(self, task: AgentTask, context: dict) -> AgentResult:
        """Execute a task and return its result. Must not raise for
        recoverable errors — return AgentResult(success=False, error=...)."""

    @abstractmethod
    async def health_check(self) -> AgentHealth: ...


class IAgentRegistry(ABC):
    """Contract for agent registration and discovery."""

    @abstractmethod
    def register(self, agent: IAgent) -> None: ...

    @abstractmethod
    def unregister(self, agent_id: str) -> None: ...

    @abstractmethod
    def get(self, agent_id: str) -> IAgent | None: ...

    @abstractmethod
    def find_by_capability(self, capability: AgentCapability) -> list[IAgent]: ...

    @abstractmethod
    def list_states(self) -> list[AgentState]: ...

    @abstractmethod
    def update_state(self, agent_id: str, state: AgentState) -> None: ...


class IAgentSelector(ABC):
    """Contract for choosing the best agent for a task."""

    @abstractmethod
    async def select(
        self, task: AgentTask, candidates: list[IAgent]
    ) -> IAgent: ...


class ITaskDispatcher(ABC):
    """Contract for dispatching a batch of tasks for execution."""

    @abstractmethod
    async def dispatch(
        self, tasks: list[AgentTask], context: dict
    ) -> list[AgentResult]: ...


class IAgentExecutor(ABC):
    """Contract for executing a single agent against a single task."""

    @abstractmethod
    async def execute(
        self, agent: IAgent, task: AgentTask, context: dict
    ) -> AgentExecution: ...


class IMessageBus(ABC):
    """Contract for the internal agent-to-agent message bus."""

    @abstractmethod
    async def publish(self, message: AgentMessage) -> None: ...

    @abstractmethod
    def subscribe(
        self, topic: str, handler: Callable[[AgentMessage], "AsyncIterator[None] | None"]
    ) -> None: ...

    @abstractmethod
    async def request(
        self, message: AgentMessage, *, timeout_seconds: float = 30.0
    ) -> AgentMessage: ...

    @abstractmethod
    async def broadcast(self, message: AgentMessage) -> None: ...


class IExecutionMonitor(ABC):
    """Contract for tracking execution progress, failures, and performance."""

    @abstractmethod
    def record_start(self, execution: AgentExecution) -> None: ...

    @abstractmethod
    def record_completion(self, execution: AgentExecution) -> None: ...

    @abstractmethod
    def get_history(self, task_id: str) -> list[AgentExecution]: ...

    @abstractmethod
    def get_active(self) -> list[AgentExecution]: ...


class IAgentCoordinator(ABC):
    """Contract for coordinating multiple agents across a dependency-ordered plan."""

    @abstractmethod
    async def coordinate(
        self, batches: list[list[AgentTask]], context: dict
    ) -> list[AgentResult]: ...