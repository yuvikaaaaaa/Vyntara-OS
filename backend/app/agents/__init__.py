"""IOS Agents — Public API.

The Agent Engine executes Planning Engine output (ExecutionPlan /
ExecutionStep batches) by selecting, dispatching to, and coordinating
specialized agent implementations, tracking their progress, and
aggregating results.

It does NOT plan (Planning Engine), does NOT retrieve (Retrieval Engine),
and does NOT generate/ground/validate LLM output (RAG Engine) — it
executes.

Usage::

    from app.agents import AgentManager, AgentTask, AgentResult
    from app.agents import AgentRegistry, AgentSelector, AgentContextBuilder
    from app.agents import TaskDispatcher, AgentExecutor, AgentCoordinator
    from app.agents import AgentCommunication, ExecutionMonitor
"""

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------
from app.agents.types import (
    AgentCapability,
    AgentExecution,
    AgentHealth,
    AgentMessage,
    AgentMetrics,
    AgentResult,
    AgentRole,
    AgentState,
    AgentStatus,
    AgentTask,
    ExecutionStatus,
    MessageType,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
from app.agents.exceptions import (
    AgentAlreadyRegisteredError,
    AgentCancelledError,
    AgentCapabilityError,
    AgentCommunicationError,
    AgentCoordinationError,
    AgentError,
    AgentExecutionError,
    AgentNotFoundError,
    AgentTimeoutError,
    AgentUnhealthyError,
    MaxRetriesExceededError,
    NoAgentAvailableError,
    TaskDispatchError,
)

# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------
from app.agents.interfaces import (
    IAgent,
    IAgentCoordinator,
    IAgentExecutor,
    IAgentRegistry,
    IAgentSelector,
    IExecutionMonitor,
    IMessageBus,
    ITaskDispatcher,
)

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
from app.agents.base import BaseAgentComponent

# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
from app.agents.agent_registry import AgentRegistry
from app.agents.agent_selector import AgentSelector
from app.agents.agent_context import AgentContextBuilder, ExecutionContext
from app.agents.task_dispatcher import TaskDispatcher
from app.agents.agent_executor import AgentExecutor
from app.agents.agent_communication import AgentCommunication
from app.agents.execution_monitor import ExecutionMonitor
from app.agents.agent_coordinator import AgentCoordinator

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
from app.agents.agent_manager import AgentManager

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    # Types
    "AgentCapability",
    "AgentRole",
    "AgentStatus",
    "ExecutionStatus",
    "MessageType",
    "AgentTask",
    "AgentMessage",
    "AgentState",
    "AgentHealth",
    "AgentResult",
    "AgentExecution",
    "AgentMetrics",
    # Exceptions
    "AgentError",
    "AgentNotFoundError",
    "AgentAlreadyRegisteredError",
    "AgentExecutionError",
    "AgentTimeoutError",
    "AgentCancelledError",
    "AgentCommunicationError",
    "AgentCapabilityError",
    "NoAgentAvailableError",
    "AgentUnhealthyError",
    "TaskDispatchError",
    "AgentCoordinationError",
    "MaxRetriesExceededError",
    # Interfaces
    "IAgent",
    "IAgentRegistry",
    "IAgentSelector",
    "ITaskDispatcher",
    "IAgentExecutor",
    "IMessageBus",
    "IExecutionMonitor",
    "IAgentCoordinator",
    # Base
    "BaseAgentComponent",
    # Components
    "AgentRegistry",
    "AgentSelector",
    "AgentContextBuilder",
    "ExecutionContext",
    "TaskDispatcher",
    "AgentExecutor",
    "AgentCommunication",
    "ExecutionMonitor",
    "AgentCoordinator",
    # Orchestrator
    "AgentManager",
]