"""IOS Planner — Planner Manager."""
from __future__ import annotations

from app.planner.base import BasePlanner
from app.planner.exceptions import InvalidPlanError, PlanningError
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
from app.planner.types import (
    ExecutionPlan,
    Goal,
    PlanningMetrics,
)


class PlannerManager(BasePlanner):
    """
    The single orchestration entry point for the Planning Engine.

    Coordinates the full pipeline:
      1. GoalParser        — raw text -> ParsedGoal
      2. TaskDecomposer     — ParsedGoal -> list[Task]
      3. PlanGenerator      — goal + tasks -> raw ExecutionPlan
      4. PlanOptimizer      — raw plan -> optimized plan (parallel batching,
                               critical-path annotation, redundancy pruning)
      5. PlanValidator      — optimized plan -> PlanValidationResult
                               (internally delegates constraint checking to
                               IConstraintSolver)
      6. ExecutionPlanner   — validated plan -> list[ExecutionStep]

    Every dependency is injected via the constructor; PlannerManager never
    instantiates a component itself and never reaches into another
    component's private state — it communicates exclusively through the
    interfaces declared in app.planner.interfaces.
    """

    def __init__(
        self,
        goal_parser: IGoalParser,
        task_decomposer: ITaskDecomposer,
        plan_generator: IPlanGenerator,
        plan_optimizer: IPlanOptimizer,
        plan_validator: IPlanValidator,
        execution_planner: IExecutionPlanner,
        *,
        fail_on_invalid_plan: bool = True,
        min_confidence_threshold: float = 0.3,
    ) -> None:
        super().__init__()
        self._goal_parser = goal_parser
        self._task_decomposer = task_decomposer
        self._plan_generator = plan_generator
        self._plan_optimizer = plan_optimizer
        self._plan_validator = plan_validator
        self._execution_planner = execution_planner
        self._fail_on_invalid_plan = fail_on_invalid_plan
        self._min_confidence_threshold = min_confidence_threshold

    async def create_plan(self, goal: Goal) -> ExecutionPlan:
        """
        Run the complete planning pipeline for a raw natural-language goal
        and return a fully validated, optimized, execution-ready plan.

        Raises:
            InvalidPlanError: The generated plan fails validation and
                               fail_on_invalid_plan is True (default), or
                               the plan's confidence falls below the
                               configured minimum threshold.
            PlanningError: Any pipeline stage raises an unrecoverable error.
        """
        async with self._span("create_plan"):
            start = self._now_ms()
            metrics = PlanningMetrics()

            try:
                stage_start = self._now_ms()
                parsed_goal = await self._goal_parser.parse(goal)
                metrics.goal_parse_latency_ms = self._elapsed_ms(stage_start)

                stage_start = self._now_ms()
                tasks = await self._task_decomposer.decompose(parsed_goal)
                metrics.decomposition_latency_ms = self._elapsed_ms(stage_start)
                metrics.total_tasks = len(tasks)

                stage_start = self._now_ms()
                plan = await self._plan_generator.generate(
                    parsed_goal, tasks, dependencies=[]
                )
                metrics.generation_latency_ms = self._elapsed_ms(stage_start)
                metrics.total_dependencies = len(plan.dependencies)

                stage_start = self._now_ms()
                plan = await self._plan_optimizer.optimize(plan)
                metrics.optimization_latency_ms = self._elapsed_ms(stage_start)

                stage_start = self._now_ms()
                validation = await self._plan_validator.validate(plan)
                metrics.validation_latency_ms = self._elapsed_ms(stage_start)

                plan.is_valid = validation.is_valid

                if not validation.is_valid and self._fail_on_invalid_plan:
                    raise InvalidPlanError(
                        f"Generated plan failed validation with "
                        f"{len(validation.errors)} error(s).",
                        details={
                            "errors": [i.message for i in validation.errors],
                            "plan_id": plan.id,
                        },
                    )

                if validation.confidence_score < self._min_confidence_threshold:
                    raise InvalidPlanError(
                        f"Plan confidence ({validation.confidence_score:.2f}) is "
                        f"below the minimum required threshold "
                        f"({self._min_confidence_threshold:.2f}).",
                        details={"plan_id": plan.id, "confidence": validation.confidence_score},
                    )

                plan.steps = await self._execution_planner.build_steps(plan)

                metrics.total_latency_ms = self._elapsed_ms(start)
                metrics.max_parallel_batch_size = self._max_batch_size(plan)
                metrics.estimated_total_duration_ms = sum(
                    t.estimated_duration_ms or 0 for t in plan.tasks
                )
                metrics.estimated_total_tokens = sum(
                    t.estimated_tokens or 0 for t in plan.tasks
                )
                plan.metadata.generation_latency_ms = metrics.total_latency_ms

                self._log.info(
                    "plan_creation_complete",
                    plan_id=plan.id,
                    tasks=metrics.total_tasks,
                    is_valid=plan.is_valid,
                    confidence=round(validation.confidence_score, 3),
                    total_latency_ms=metrics.total_latency_ms,
                )
                return plan

            except InvalidPlanError:
                raise
            except PlanningError:
                raise
            except Exception as exc:
                raise PlanningError(f"Planning pipeline failed unexpectedly: {exc}") from exc

    async def revise_plan(
        self, plan: ExecutionPlan, feedback: str
    ) -> ExecutionPlan:
        """
        Re-run planning from the original goal with additional feedback
        appended, producing a revised plan (new version).

        Used when downstream validation (e.g. human-in-the-loop rejection,
        constraint failure discovered at execution time) requires the
        planner to try again with more context.
        """
        async with self._span("revise_plan", plan_id=plan.id):
            if plan.goal is None:
                raise InvalidPlanError(
                    "Cannot revise a plan with no attached goal.",
                    details={"plan_id": plan.id},
                )
            revised_goal = Goal(
                text=f"{plan.goal.raw_text}\n\nAdditional feedback: {feedback}",
                user_id=None,
                context={"revision_of_plan_id": plan.id},
            )
            revised = await self.create_plan(revised_goal)
            revised.version = plan.version + 1
            self._log.info(
                "plan_revised", original_plan_id=plan.id, revised_plan_id=revised.id
            )
            return revised

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _max_batch_size(plan: ExecutionPlan) -> int:
        if not plan.steps:
            return 0
        counts: dict[int, int] = {}
        for step in plan.steps:
            counts[step.batch_index] = counts.get(step.batch_index, 0) + 1
        return max(counts.values(), default=0)