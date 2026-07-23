"""IOS Planner — Public API.

The Planning Engine converts natural-language goals into validated,
optimized, execution-ready plans.

It does NOT execute anything — that is the responsibility of the future
Agent Engine, which consumes ExecutionPlan / ExecutionStep objects
produced here.

Usage::

    from app.planner import PlannerManager, Goal
    from app.planner import GoalParser, TaskDecomposer, PlanGenerator
    from app.planner import DependencyGraph, ConstraintSolver
    from app.planner import PlanOptimizer, PlanValidator, ExecutionPlanner
"""

# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------
from app.planner.types import (
    Constraint,
    ConstraintType,
    ExecutionMode,
    ExecutionPlan,
    ExecutionStep,
    Goal,
    ParsedGoal,
    PlanMetadata,
    PlanningMetrics,
    PlanValidationResult,
    SuccessCriterion,
    Task,
    TaskDependency,
    TaskPriority,
    TaskStatus,
    TaskType,
    ValidationIssue,
    ValidationSeverity,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
from app.planner.exceptions import (
    ConstraintViolationError,
    DependencyCycleError,
    EmptyPlanError,
    ExecutionPlanningError,
    GoalParsingError,
    InvalidPlanError,
    PlanGenerationError,
    PlanningError,
    PlanOptimizationError,
    PlanValidationError,
    TaskDecompositionError,
    UnresolvableDependencyError,
)

# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------
from app.planner.interfaces import (
    IConstraintSolver,
    IDependencyGraph,
    IExecutionPlanner,
    IGoalParser,
    IPlanGenerator,
    IPlanOptimizer,
    IPlanValidator,
    ITaskDecomposer,
)

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
from app.planner.base import BasePlanner

# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
from app.planner.goal_parser import GoalParser
from app.planner.task_decomposer import TaskDecomposer
from app.planner.dependency_graph import DependencyGraph
from app.planner.constraint_solver import ConstraintSolver
from app.planner.plan_generator import PlanGenerator
from app.planner.plan_optimizer import PlanOptimizer
from app.planner.plan_validator import PlanValidator
from app.planner.execution_planner import ExecutionPlanner

# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
from app.planner.planner_manager import PlannerManager

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
__all__ = [
    # Types
    "TaskStatus",
    "TaskPriority",
    "TaskType",
    "ExecutionMode",
    "ConstraintType",
    "ValidationSeverity",
    "Goal",
    "Constraint",
    "SuccessCriterion",
    "ParsedGoal",
    "TaskDependency",
    "Task",
    "ExecutionStep",
    "PlanMetadata",
    "ExecutionPlan",
    "ValidationIssue",
    "PlanValidationResult",
    "PlanningMetrics",
    # Exceptions
    "PlanningError",
    "GoalParsingError",
    "TaskDecompositionError",
    "PlanGenerationError",
    "DependencyCycleError",
    "ConstraintViolationError",
    "InvalidPlanError",
    "PlanOptimizationError",
    "PlanValidationError",
    "ExecutionPlanningError",
    "UnresolvableDependencyError",
    "EmptyPlanError",
    # Interfaces
    "IGoalParser",
    "ITaskDecomposer",
    "IPlanGenerator",
    "IPlanOptimizer",
    "IDependencyGraph",
    "IConstraintSolver",
    "IExecutionPlanner",
    "IPlanValidator",
    # Base
    "BasePlanner",
    # Components
    "GoalParser",
    "TaskDecomposer",
    "DependencyGraph",
    "ConstraintSolver",
    "PlanGenerator",
    "PlanOptimizer",
    "PlanValidator",
    "ExecutionPlanner",
    # Orchestrator
    "PlannerManager",
]