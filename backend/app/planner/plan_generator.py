"""IOS Planner — Plan Generator."""
from __future__ import annotations

from app.planner.base import BasePlanner
from app.planner.exceptions import PlanGenerationError
from app.planner.interfaces import IPlanGenerator
from app.planner.types import (
    ExecutionPlan,
    ParsedGoal,
    PlanMetadata,
    Task,
    TaskDependency,
)


class PlanGenerator(BasePlanner, IPlanGenerator):
    """
    Assembles the raw ExecutionPlan object from the parsed goal, the
    decomposed task set, and the resolved dependency edges.

    This component performs structural assembly only — it does not
    validate correctness (PlanValidator), optimize scheduling
    (PlanOptimizer), or resolve dependency names to ids (that resolution
    happens here since it is a pure data-transformation step, not a
    graph algorithm).
    """

    async def generate(
        self,
        goal: ParsedGoal,
        tasks: list[Task],
        dependencies: list[TaskDependency],
    ) -> ExecutionPlan:
        async with self._span("generate_plan", tasks=str(len(tasks))):
            start = self._now_ms()
            try:
                if not tasks:
                    raise PlanGenerationError(
                        "Cannot generate a plan with zero tasks."
                    )

                resolved_dependencies = self._resolve_name_dependencies(tasks, dependencies)

                plan = ExecutionPlan(
                    goal=goal,
                    tasks=tasks,
                    dependencies=resolved_dependencies,
                    metadata=PlanMetadata(
                        source_goal_id=goal.id,
                        generation_latency_ms=self._elapsed_ms(start),
                    ),
                )
                self._log.info(
                    "plan_generated",
                    plan_id=plan.id,
                    tasks=len(tasks),
                    dependencies=len(resolved_dependencies),
                )
                return plan
            except PlanGenerationError:
                raise
            except Exception as exc:
                raise PlanGenerationError(f"Plan generation failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Dependency name resolution
    # ------------------------------------------------------------------

    def _resolve_name_dependencies(
        self, tasks: list[Task], explicit_dependencies: list[TaskDependency]
    ) -> list[TaskDependency]:
        """
        Merge explicitly-supplied TaskDependency edges with dependencies
        implied by each Task's ``metadata["depends_on_names"]`` field
        (populated by TaskDecomposer), resolving names to task ids.

        Unresolvable names are silently skipped here — ConstraintSolver
        is responsible for surfacing them as violations.
        """
        name_to_id = {t.name: t.id for t in tasks}
        resolved: list[TaskDependency] = list(explicit_dependencies)
        seen_pairs = {(d.task_id, d.depends_on_id) for d in resolved}

        for task in tasks:
            depends_on_names = task.metadata.get("depends_on_names", [])
            for dep_name in depends_on_names:
                dep_id = name_to_id.get(dep_name)
                if dep_id is None or dep_id == task.id:
                    continue
                pair = (task.id, dep_id)
                if pair not in seen_pairs:
                    resolved.append(TaskDependency(task_id=task.id, depends_on_id=dep_id))
                    seen_pairs.add(pair)

        return resolved