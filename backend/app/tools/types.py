"""IOS Tools — Shared Types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ToolType(str, Enum):
    """Broad category a tool belongs to — used for grouping/discovery."""
    CODE_EXECUTION = "code_execution"
    DATA_ACCESS = "data_access"
    NETWORK = "network"
    FILESYSTEM = "filesystem"
    VISION = "vision"
    GENERATION = "generation"
    UTILITY = "utility"


class ToolCapability(str, Enum):
    """Fine-grained capability a tool declares, used for capability-based selection."""
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    SEARCH = "search"
    ANALYZE = "analyze"
    GENERATE = "generate"
    TRANSFORM = "transform"


class ToolStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    PERMISSION_DENIED = "permission_denied"


class SandboxResourceLimit(str, Enum):
    """Named resource dimensions a sandbox implementation may enforce."""
    CPU_SECONDS = "cpu_seconds"
    MEMORY_MB = "memory_mb"
    WALL_TIME_SECONDS = "wall_time_seconds"
    NETWORK_BYTES = "network_bytes"
    DISK_BYTES = "disk_bytes"


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------


@dataclass
class ToolMetadata:
    """Static descriptor for a registered tool — its identity, schema, and policy."""
    name: str
    display_name: str
    description: str
    tool_type: ToolType
    capabilities: list[ToolCapability] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    required_permission: str | None = None
    default_timeout_seconds: int = 60
    max_timeout_seconds: int = 300
    max_retries: int = 2
    resource_limits: dict[SandboxResourceLimit, float] = field(default_factory=dict)
    version: str = "1.0"
    tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------


@dataclass
class ToolRequest:
    """A single invocation request against a named tool."""
    id: str = field(default_factory=lambda: str(uuid4()))
    tool_name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int | None = None
    caller_id: str | None = None          # agent_id / user_id issuing the call
    permissions: list[str] = field(default_factory=list)
    correlation_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResponse:
    """Normalized result of a tool invocation."""
    request_id: str
    tool_name: str
    success: bool
    output: Any = None
    output_text: str | None = None
    error: str | None = None
    error_code: str | None = None
    duration_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


# ---------------------------------------------------------------------------
# Execution record
# ---------------------------------------------------------------------------


@dataclass
class ToolExecution:
    """Full lifecycle record of a single tool invocation attempt."""
    id: str = field(default_factory=lambda: str(uuid4()))
    request: ToolRequest = field(default_factory=ToolRequest)
    status: ToolStatus = ToolStatus.PENDING
    attempt: int = 1
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    response: ToolResponse | None = None
    error: str | None = None
    resource_usage: dict[SandboxResourceLimit, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Health / metrics
# ---------------------------------------------------------------------------


@dataclass
class ToolHealth:
    tool_name: str
    is_healthy: bool
    status: ToolStatus | None = None
    latency_ms: float | None = None
    error: str | None = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass
class ToolMetrics:
    tool_name: str
    total_executions: int = 0
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    avg_latency_ms: float = 0.0
    current_in_flight: int = 0