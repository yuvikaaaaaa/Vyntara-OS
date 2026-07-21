"""IOS Planner — Shared Types."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"           # dependencies satisfied, not yet running
    RUNNING = "running"
    BLOCKED = "blocked"       # waiting on unsatisfied dependency/constraint
    COMPLETE = "complete"
    FAILED = "failed"
    SKIPPED = "skipped"


class TaskPriority(int, Enum):
    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


class TaskType(str, Enum):
    RESEARCH = "research"
    CODE = "code"
    ANALYSIS = "analysis"
    TOOL_CALL = "tool_call"
    GENERATION = "generation"
    VALIDATION = "validation"
    GENERIC = "generic"


class ExecutionMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


class ConstraintType(str, Enum):
    DEADLINE = "deadline"
    RESOURCE_LIMIT = "resource_limit"
    PREREQUISITE_ORDER = "prerequisite_order"
    SCHEDULING = "scheduling"
    DEPENDENCY = "dependency"


# ---------------------------------------------------------------------------
# Goal parsing
# ---------------------------------------------------------------------------


@dataclass
class Goal:
    """Raw natural-language goal submitted by a caller."""
    text: str
    user_id: UUID | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class SuccessCriterion:
    description: str
    measurable: bool = False


@dataclass
class Constraint:
    type: ConstraintType
    description: str
    value: Any = None   # e.g. deadline datetime, resource limit number


@dataclass
class ParsedGoal:
    """Structured objective extracted from a raw Goal."""
    id: str = field(default_factory=lambda: str(uuid4()))
    objective: str = ""
    constraints: list[Constraint] = field(default_factory=list)
    deadline: datetime | None = None
    resources: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    success_criteria: list[SuccessCriterion] = field(default_factory=list)
    raw_text: str = ""
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Task decomposition
# ---------------------------------------------------------------------------


@dataclass
class TaskDependency:
    """A directed dependency: task depends_on another task."""
    task_id: str
    depends_on_id: str
    is_hard: bool = True   # hard = must complete first; soft = preferred order


@dataclass
class Task:
    """An atomic, executable unit of work."""
    id: str = field(default_factory=lambda: str(uuid4()))
    name: str = ""
    description: str = ""
    task_type: TaskType = TaskType.GENERIC
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    parent_id: str | None = None
    estimated_duration_ms: int | None = None
    estimated_tokens: int | None = None
    required_capabilities: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    max_retries: int = 3
    timeout_seconds: int = 120


# ---------------------------------------------------------------------------
# Execution plan
# ---------------------------------------------------------------------------


@dataclass
class ExecutionStep:
    """A single step in the final execution-ready ordering."""
    task: Task
    batch_index: int              # steps sharing a batch_index run in parallel
    execution_mode: ExecutionMode = ExecutionMode.SEQUENTIAL
    retry_strategy: dict[str, Any] = field(default_factory=dict)
    rollback_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanMetadata:
    planner_version: str = "1.0"
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    generation_latency_ms: int | None = None
    optimization_applied: bool = False
    source_goal_id: str | None = None


@dataclass
class ExecutionPlan:
    """The complete, executable plan produced by the Planning Engine."""
    id: str = field(default_factory=lambda: str(uuid4()))
    goal: ParsedGoal | None = None
    tasks: list[Task] = field(default_factory=list)
    dependencies: list[TaskDependency] = field(default_factory=list)
    steps: list[ExecutionStep] = field(default_factory=list)
    metadata: PlanMetadata = field(default_factory=PlanMetadata)
    version: int = 1
    is_valid: bool = False


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationIssue:
    severity: ValidationSeverity
    code: str
    message: str
    task_id: str | None = None


@dataclass
class PlanValidationResult:
    is_valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    confidence_score: float = 0.0

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class PlanningMetrics:
    total_tasks: int = 0
    total_dependencies: int = 0
    max_parallel_batch_size: int = 0
    critical_path_length: int = 0
    estimated_total_duration_ms: int = 0
    estimated_total_tokens: int = 0
    goal_parse_latency_ms: int = 0
    decomposition_latency_ms: int = 0
    generation_latency_ms: int = 0
    optimization_latency_ms: int = 0
    validation_latency_ms: int = 0
    total_latency_ms: int = 0