"""IOS Planner — Plan Validator."""
from __future__ import annotations

from app.planner.base import BasePlanner
from app.planner.dependency_graph import DependencyGraph
from app.planner.interfaces import IConstraintSolver, IPlanValidator
from app.planner.types import ExecutionPlan, PlanValidationResult, ValidationSeverity


class PlanValidator(BasePlanner, IPlanValidator):
    """
    Final quality gate for an ExecutionPlan before it is handed to
    ExecutionPlanner.

    Validates:
    - Dependency graph correctness (no cycles, all references resolvable)
    - Completeness (every task has a name/description, goal is attached)
    - Executable ordering (a valid topological order exists)
    - Constraint satisfaction (delegated to the injected IConstraintSolver)
    - Overall plan confidence score, derived from goal-parse confidence,
      constraint violation count, and structural issue count

    Communicates only through IConstraintSolver and DependencyGraph (the
    latter instantiated locally since it is a stateless algorithmic
    utility, not an injected service) — never touches any other module.
    """

    def __init__(
        self,
        constraint_solver: IConstraintSolver,
        dependency_graph_factory=DependencyGraph,
    ) -> None:
        super().__init__()
        self._constraint_solver = constraint_solver
        self._graph_factory = dependency_graph_factory

    async def validate(self, plan: ExecutionPlan) -> PlanValidationResult:
        async with self._span("validate_plan", tasks=str(len(plan.tasks))):
            start = self._now_ms()
            issues = []

            issues.extend(self._validate_completeness(plan))

            graph = self._graph_factory()
            graph.build(plan.tasks, plan.dependencies)
            issues.extend(self._validate_graph(graph))

            if plan.goal is not None:
                violation_messages = await self._constraint_solver.solve(
                    plan.goal, plan.tasks
                )
                issues.extend(
                    self.warning("CONSTRAINT_VIOLATION", msg) for msg in violation_messages
                )

            has_errors = any(i.severity == ValidationSeverity.ERROR for i in issues)
            confidence = self._compute_confidence(plan, issues)

            result = PlanValidationResult(
                is_valid=not has_errors,
                issues=issues,
                confidence_score=confidence,
            )
            self._log.info(
                "plan_validated",
                plan_id=plan.id,
                is_valid=result.is_valid,
                errors=len(result.errors),
                warnings=len(result.warnings),
                confidence=round(confidence, 3),
                latency_ms=self._elapsed_ms(start),
            )
            return result

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _validate_completeness(self, plan: ExecutionPlan) -> list:
        issues = []
        if not plan.tasks:
            issues.append(self.error("EMPTY_PLAN", "Plan contains no tasks."))
            return issues

        if plan.goal is None:
            issues.append(
                self.warning("MISSING_GOAL", "Plan has no attached ParsedGoal.")
            )

        for task in plan.tasks:
            if not task.name or not task.name.strip():
                issues.append(
                    self.error("EMPTY_TASK_NAME", "Task has an empty name.", task.id)
                )
            if not task.description or not task.description.strip():
                issues.append(
                    self.warning(
                        "EMPTY_TASK_DESCRIPTION",
                        f"Task '{task.name}' has no description.",
                        task.id,
                    )
                )
            if task.timeout_seconds <= 0:
                issues.append(
                    self.error(
                        "INVALID_TIMEOUT",
                        f"Task '{task.name}' has a non-positive timeout_seconds.",
                        task.id,
                    )
                )
        return issues

    def _validate_graph(self, graph: DependencyGraph) -> list:
        issues = []
        cycles = graph.detect_cycles()
        if cycles:
            for cycle in cycles:
                issues.append(
                    self.error(
                        "DEPENDENCY_CYCLE",
                        f"Cycle detected: {' -> '.join(cycle)}",
                    )
                )
            return issues  # topological order is meaningless with a cycle

        try:
            order = graph.topological_order()
            if not order:
                issues.append(
                    self.warning(
                        "EMPTY_TOPOLOGICAL_ORDER",
                        "Dependency graph produced an empty execution order.",
                    )
                )
        except Exception as exc:
            issues.append(
                self.error(
                    "TOPOLOGICAL_SORT_FAILED",
                    f"Failed to compute a valid execution order: {exc}",
                )
            )
        return issues

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    def _compute_confidence(self, plan: ExecutionPlan, issues: list) -> float:
        base = plan.goal.confidence if plan.goal else 0.5

        error_count = sum(1 for i in issues if i.severity == ValidationSeverity.ERROR)
        warning_count = sum(1 for i in issues if i.severity == ValidationSeverity.WARNING)

        penalty = min(0.6, error_count * 0.25 + warning_count * 0.05)
        confidence = max(0.0, min(1.0, base - penalty))
        return confidence