"""IOS Planner — Exceptions."""
from __future__ import annotations

from app.core.exceptions import IosBaseException


class PlanningError(IosBaseException):
    http_status = 500
    code = "PLANNING_ERROR"


class GoalParsingError(PlanningError):
    http_status = 422
    code = "GOAL_PARSING_ERROR"


class TaskDecompositionError(PlanningError):
    code = "TASK_DECOMPOSITION_ERROR"


class PlanGenerationError(PlanningError):
    code = "PLAN_GENERATION_ERROR"


class DependencyCycleError(PlanningError):
    http_status = 422
    code = "DEPENDENCY_CYCLE_DETECTED"


class ConstraintViolationError(PlanningError):
    http_status = 422
    code = "CONSTRAINT_VIOLATION"


class InvalidPlanError(PlanningError):
    http_status = 422
    code = "INVALID_PLAN"


class PlanOptimizationError(PlanningError):
    code = "PLAN_OPTIMIZATION_ERROR"


class PlanValidationError(PlanningError):
    http_status = 422
    code = "PLAN_VALIDATION_ERROR"


class ExecutionPlanningError(PlanningError):
    code = "EXECUTION_PLANNING_ERROR"


class UnresolvableDependencyError(PlanningError):
    http_status = 422
    code = "UNRESOLVABLE_DEPENDENCY"


class EmptyPlanError(PlanningError):
    http_status = 422
    code = "EMPTY_PLAN"