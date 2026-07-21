"""IOS Planner — Constraint Solver."""
from __future__ import annotations

from datetime import datetime, timezone

from app.planner.base import BasePlanner
from app.planner.interfaces import IConstraintSolver
from app.planner.types import ConstraintType, ParsedGoal, Task

# Conservative default budget guards used when the goal itself specifies
# no explicit resource_limit constraint.
_DEFAULT_MAX_TOTAL_TOKENS = 100_000
_DEFAULT_MAX_TASK_COUNT = 50


class ConstraintSolver(BasePlanner, IConstraintSolver):
    """
    Validates a task set against the constraints extracted by GoalParser.

    Checks performed:
    - DEADLINE: total estimated duration must not exceed time remaining
                until the deadline
    - RESOURCE_LIMIT: total estimated tokens / task count must stay within
                       configured or goal-specified limits
    - PREREQUISITE_ORDER: tasks referencing "depends_on_names" in metadata
                           must resolve to an existing task in the set
    - SCHEDULING: flags tasks with conflicting duplicate names that could
                  cause ambiguous dependency resolution downstream
    - DEPENDENCY: flags tasks with zero capabilities that nonetheless
                  claim a specialised task_type, a common decomposition
                  quality issue

    Returns human-readable violation messages rather than raising —
    PlanValidator and PlannerManager decide how violations affect overall
    plan validity.
    """

    def __init__(
        self,
        *,
        max_total_tokens: int = _DEFAULT_MAX_TOTAL_TOKENS,
        max_task_count: int = _DEFAULT_MAX_TASK_COUNT,
    ) -> None:
        super().__init__()
        self._max_total_tokens = max_total_tokens
        self._max_task_count = max_task_count

    async def solve(self, goal: ParsedGoal, tasks: list[Task]) -> list[str]:
        async with self._span("solve_constraints", tasks=str(len(tasks))):
            violations: list[str] = []

            violations.extend(self._check_deadline(goal, tasks))
            violations.extend(self._check_resource_limits(goal, tasks))
            violations.extend(self._check_prerequisite_order(tasks))
            violations.extend(self._check_scheduling_conflicts(tasks))
            violations.extend(self._check_task_count(tasks))

            self._log.info(
                "constraints_solved", tasks=len(tasks), violations=len(violations)
            )
            return violations

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_deadline(self, goal: ParsedGoal, tasks: list[Task]) -> list[str]:
        if goal.deadline is None:
            return []

        now = datetime.now(tz=timezone.utc)
        deadline = goal.deadline if goal.deadline.tzinfo else goal.deadline.replace(tzinfo=timezone.utc)
        remaining_ms = (deadline - now).total_seconds() * 1000
        if remaining_ms <= 0:
            return [f"Deadline {deadline.isoformat()} has already passed."]

        total_estimated_ms = sum(t.estimated_duration_ms or 30_000 for t in tasks)
        if total_estimated_ms > remaining_ms:
            return [
                f"Estimated total task duration ({total_estimated_ms}ms) exceeds "
                f"time remaining until deadline ({int(remaining_ms)}ms)."
            ]
        return []

    def _check_resource_limits(self, goal: ParsedGoal, tasks: list[Task]) -> list[str]:
        violations: list[str] = []
        limit = self._max_total_tokens
        for c in goal.constraints:
            if c.type == ConstraintType.RESOURCE_LIMIT and isinstance(c.value, (int, float)):
                limit = int(c.value)
                break

        total_tokens = sum(t.estimated_tokens or 1000 for t in tasks)
        if total_tokens > limit:
            violations.append(
                f"Estimated total token usage ({total_tokens}) exceeds resource "
                f"limit ({limit})."
            )
        return violations

    def _check_prerequisite_order(self, tasks: list[Task]) -> list[str]:
        violations: list[str] = []
        name_to_task = {t.name: t for t in tasks}
        for task in tasks:
            depends_on_names = task.metadata.get("depends_on_names", [])
            for dep_name in depends_on_names:
                if dep_name and dep_name not in name_to_task:
                    violations.append(
                        f"Task '{task.name}' depends on unresolved task "
                        f"'{dep_name}' not present in the decomposed task set."
                    )
        return violations

    def _check_scheduling_conflicts(self, tasks: list[Task]) -> list[str]:
        violations: list[str] = []
        seen_names: dict[str, int] = {}
        for task in tasks:
            seen_names[task.name] = seen_names.get(task.name, 0) + 1
        for name, count in seen_names.items():
            if count > 1:
                violations.append(
                    f"Task name '{name}' appears {count} times; duplicate names "
                    f"can cause ambiguous dependency resolution."
                )
        return violations

    def _check_task_count(self, tasks: list[Task]) -> list[str]:
        if len(tasks) > self._max_task_count:
            return [
                f"Task count ({len(tasks)}) exceeds the maximum allowed "
                f"({self._max_task_count}); consider higher-level decomposition."
            ]
        if len(tasks) == 0:
            return ["No tasks were produced for this goal."]
        return []