"""IOS Agents — Shared Types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AgentCapability(str, Enum):
    RESEARCH = "research"
    CODE_GENERATION = "code_generation"
    CODE_EXECUTION = "code_execution"
    ANALYSIS = "analysis"
    VISION = "vision"
    SQL = "sql"
    TOOL_USE = "tool_use"
    GENERATION = "generation"
    VALIDATION = "validation"
    REFLECTION = "reflection"
    PLANNING = "planning"


class AgentRole(str, Enum):
    PLANNER = "planner"
    SUPERVISOR = "supervisor"
    RESEARCH = "research"
    CODING = "coding"
    VISION = "vision"
    SQL = "sql"
    ML = "ml"
    MEMORY = "memory"
    REFLECTION = "reflection"
    DEBATE = "debate"
    EVALUATION = "evaluation"
    REPORT = "report"
    GENERIC = "generic"


class AgentStatus(str, Enum):
    IDLE = "idle"
    BUSY = "busy"
    UNHEALTHY = "unhealthy"
    OFFLINE = "offline"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class MessageType(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    BROADCAST = "broadcast"
    EVENT = "event"


# ---------------------------------------------------------------------------
# Agent task
# ---------------------------------------------------------------------------


@dataclass
class AgentTask:
    """A unit of work assigned to an agent (mirrors planner.types.Task fields
    the agent layer cares about, decoupled from the Planning Engine's own type)."""
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    description: str = ""
    required_capabilities: list[AgentCapability] = field(default_factory=list)
    priority: int = 5
    inputs: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 120
    max_retries: int = 3
    batch_index: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Messaging
# ---------------------------------------------------------------------------


@dataclass
class AgentMessage:
    """A message exchanged on the internal agent message bus."""
    id: str = field(default_factory=lambda: str(uuid4()))
    type: MessageType = MessageType.EVENT
    topic: str = ""
    sender: str = ""
    recipient: str | None = None   # None for broadcast
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# Agent state / registration
# ---------------------------------------------------------------------------


@dataclass
class AgentState:
    """Current runtime state of a registered agent instance."""
    agent_id: str
    role: AgentRole
    status: AgentStatus = AgentStatus.IDLE
    active_task_count: int = 0
    last_heartbeat: datetime | None = None


@dataclass
class AgentHealth:
    agent_id: str
    is_healthy: bool
    status: AgentStatus
    latency_ms: float | None = None
    error: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


@dataclass
class AgentResult:
    """Output produced by a single agent execution."""
    task_id: str
    agent_id: str
    success: bool
    output: Any = None
    output_text: str | None = None
    error: str | None = None
    tokens_used: int | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentExecution:
    """Full lifecycle record of one agent's attempt at one task."""
    id: str = field(default_factory=lambda: str(uuid4()))
    task: AgentTask = field(default_factory=AgentTask)
    agent_id: str = ""
    status: ExecutionStatus = ExecutionStatus.PENDING
    attempt: int = 1
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    result: AgentResult | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class AgentMetrics:
    agent_id: str
    total_executions: int = 0
    success_count: int = 0
    failure_count: int = 0
    avg_latency_ms: float = 0.0
    avg_tokens_used: float = 0.0
    current_workload: int = 0