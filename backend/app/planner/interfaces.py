"""IOS Planner — Interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.planner.types import (
    ExecutionPlan,
    ExecutionStep,
    Goal,
    ParsedGoal,
    PlanValidationResult,
    Task,
    TaskDependency,
)


class IGoalParser(ABC):
    """Contract for natural-language goal parsing implementations."""

    @abstractmethod
    async def parse(self, goal: Goal) -> ParsedGoal:
        """Convert a raw natural-language goal into a structured ParsedGoal."""


class ITaskDecomposer(ABC):
    """Contract for goal-to-task decomposition implementations."""

    @abstractmethod
    async def decompose(self, goal: ParsedGoal) -> list[Task]:
        """Break a parsed goal into atomic, executable tasks."""


class IDependencyGraph(ABC):
    """Contract for task dependency graph implementations."""

    @abstractmethod
    def build(self, tasks: list[Task], dependencies: list[TaskDependency]) -> None:
        """Construct the internal graph representation."""

    @abstractmethod
    def topological_order(self) -> list[str]:
        """Return task ids in a valid topological (dependency-respecting) order."""

    @abstractmethod
    def detect_cycles(self) -> list[list[str]]:
        """Return a list of cycles found (each a list of task ids), empty if none."""

    @abstractmethod
    def critical_path(self) -> list[str]:
        """Return the task id sequence forming the longest dependency chain."""


class IConstraintSolver(ABC):
    """Contract for plan constraint validation implementations."""

    @abstractmethod
    async def solve(self, goal: ParsedGoal, tasks: list[Task]) -> list[str]:
        """Return a list of human-readable constraint violation messages (empty if none)."""


class IPlanGenerator(ABC):
    """Contract for execution-plan generation implementations."""

    @abstractmethod
    async def generate(
        self,
        goal: ParsedGoal,
        tasks: list[Task],
        dependencies: list[TaskDependency],
    ) -> ExecutionPlan:
        """Produce an ExecutionPlan from parsed goal, tasks, and dependencies."""


class IPlanOptimizer(ABC):
    """Contract for plan optimization implementations."""

    @abstractmethod
    async def optimize(self, plan: ExecutionPlan) -> ExecutionPlan:
        """Return an optimized (e.g. maximally parallelised) copy of the plan."""


class IPlanValidator(ABC):
    """Contract for plan validation implementations."""

    @abstractmethod
    async def validate(self, plan: ExecutionPlan) -> PlanValidationResult:
        """Validate completeness, dependency consistency, and constraints."""


class IExecutionPlanner(ABC):
    """Contract for converting a validated plan into execution-ready steps."""

    @abstractmethod
    async def build_steps(self, plan: ExecutionPlan) -> list[ExecutionStep]:
        """Generate ordered/batched ExecutionStep objects with retry metadata."""