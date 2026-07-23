"""IOS Planner — Execution Planner."""
from __future__ import annotations

from app.planner.base import BasePlanner
from app.planner.dependency_graph import DependencyGraph
from app.planner.exceptions import ExecutionPlanningError
from app.planner.interfaces import IExecutionPlanner
from app.planner.types import ExecutionMode, ExecutionPlan, ExecutionStep, Task

# Default retry backoff schedule (seconds) applied to every generated step
# unless a task-specific override exists in Task.metadata.
_DEFAULT_RETRY_BACKOFF_SECONDS = [1, 2, 4]


class ExecutionPlanner(BasePlanner, IExecutionPlanner):
    """
    Converts a validated ExecutionPlan into a concrete, ordered list of
    ExecutionStep objects ready to be handed to an execution runtime
    (e.g. a future Agent Engine / LangGraph workflow).

    Responsibilities:
    - Compute parallel execution batches via DependencyGraph
    - Assign ExecutionMode.PARALLEL to any batch containing more than one
      task, ExecutionMode.SEQUENTIAL to single-task batches
    - Attach a retry strategy (max attempts, backoff schedule) per step,
      honouring any task-specific override
    - Attach rollback metadata describing how to safely undo a step's
      side effects if a later step in the plan fails (best-effort —
      populated from task metadata when the decomposer/generator
      supplied compensating-action hints, otherwise a safe no-op default)

    This is a pure transformation step — it does not execute anything
    and does not call AI Core, Retrieval, or any external system.
    """

    def __init__(self, dependency_graph_factory=DependencyGraph) -> None:
        super().__init__()
        self._graph_factory = dependency_graph_factory

    async def build_steps(self, plan: ExecutionPlan) -> list[ExecutionStep]:
        async with self._span("build_execution_steps", tasks=str(len(plan.tasks))):
            try:
                if not plan.tasks:
                    raise ExecutionPlanningError(
                        "Cannot build execution steps for a plan with zero tasks."
                    )

                graph = self._graph_factory()
                graph.build(plan.tasks, plan.dependencies)
                batches = graph.parallel_batches()

                task_by_id = {t.id: t for t in plan.tasks}
                steps: list[ExecutionStep] = []

                for batch_index, batch_task_ids in enumerate(batches):
                    mode = (
                        ExecutionMode.PARALLEL
                        if len(batch_task_ids) > 1
                        else ExecutionMode.SEQUENTIAL
                    )
                    for task_id in batch_task_ids:
                        task = task_by_id[task_id]
                        step = ExecutionStep(
                            task=task,
                            batch_index=batch_index,
                            execution_mode=mode,
                            retry_strategy=self._build_retry_strategy(task),
                            rollback_metadata=self._build_rollback_metadata(task),
                        )
                        steps.append(step)

                self._log.info(
                    "execution_steps_built",
                    plan_id=plan.id,
                    steps=len(steps),
                    batches=len(batches),
                )
                return steps
            except ExecutionPlanningError:
                raise
            except Exception as exc:
                raise ExecutionPlanningError(
                    f"Failed to build execution steps: {exc}"
                ) from exc

    # ------------------------------------------------------------------
    # Retry strategy
    # ------------------------------------------------------------------

    def _build_retry_strategy(self, task: Task) -> dict:
        override = task.metadata.get("retry_backoff_seconds")
        backoff = override if isinstance(override, list) else _DEFAULT_RETRY_BACKOFF_SECONDS
        return {
            "max_attempts": max(1, task.max_retries),
            "backoff_seconds": backoff[: max(1, task.max_retries)],
            "retry_on": task.metadata.get(
                "retry_on", ["timeout", "provider_unavailable", "transient_error"]
            ),
        }

    # ------------------------------------------------------------------
    # Rollback metadata
    # ------------------------------------------------------------------

    def _build_rollback_metadata(self, task: Task) -> dict:
        compensating_action = task.metadata.get("compensating_action")
        if compensating_action:
            return {
                "supported": True,
                "action": compensating_action,
                "reason": "Explicit compensating action supplied by decomposition.",
            }
        # Read-only / generative tasks (research, analysis, generation) are
        # typically safe to simply discard on failure with no rollback
        # action required; mutating tasks (tool_call) are flagged as
        # requiring manual review since we cannot infer the side effect.
        requires_review = task.task_type.value == "tool_call"
        return {
            "supported": not requires_review,
            "action": None,
            "reason": (
                "Tool-call task has unknown side effects; manual rollback review required."
                if requires_review
                else "No side effects assumed; safe to discard on failure."
            ),
        }