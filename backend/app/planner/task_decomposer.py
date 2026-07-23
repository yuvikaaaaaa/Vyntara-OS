"""IOS Planner — Task Decomposer."""
from __future__ import annotations

import json
import re

from app.ai_core.router.model_router import ModelRouter
from app.ai_core.types import ChatMessage, ChatRequest, GenerationConfig, ModelCapability, RoutingContext
from app.planner.base import BasePlanner
from app.planner.exceptions import TaskDecompositionError
from app.planner.interfaces import ITaskDecomposer
from app.planner.types import ParsedGoal, Task, TaskType

_DECOMPOSE_INSTRUCTIONS = """Decompose the following objective into a minimal set of \
atomic, independently-executable tasks. Each task should be small enough to be handled \
by a single specialized agent. Identify which tasks can be done in parallel vs must be \
sequential by noting dependencies.

Return ONLY valid JSON, no explanation, no markdown fences:

{
  "tasks": [
    {
      "name": "short task name",
      "description": "what this task must accomplish",
      "task_type": "research|code|analysis|tool_call|generation|validation|generic",
      "depends_on": ["task name(s) this requires, empty if none"],
      "required_capabilities": ["capability1"]
    }
  ]
}

Objective: {objective}
Constraints: {constraints}
Max tasks: {max_tasks}"""

_MAX_DECOMPOSITION_DEPTH = 3


class TaskDecomposer(BasePlanner, ITaskDecomposer):
    """
    Breaks a ParsedGoal into atomic, executable Task objects.

    Uses AI Core's ModelRouter for LLM-assisted decomposition. Supports
    recursive decomposition — any task whose description still appears
    compound (multiple verbs / "and" conjunctions) is recursively broken
    down up to a configurable max depth, producing hierarchical subtasks
    linked via Task.parent_id.

    Parallel vs sequential structuring is expressed through the returned
    dependency hints (consumed by DependencyGraph downstream) rather than
    an explicit execution_mode on the Task itself.
    """

    def __init__(
        self,
        model_router: ModelRouter,
        *,
        model_id: str | None = None,
        max_tasks_per_call: int = 8,
        max_depth: int = _MAX_DECOMPOSITION_DEPTH,
    ) -> None:
        super().__init__()
        self._router = model_router
        self._model_id = model_id
        self._max_tasks = max_tasks_per_call
        self._max_depth = max_depth

    async def decompose(self, goal: ParsedGoal) -> list[Task]:
        async with self._span("decompose"):
            try:
                tasks, _name_to_id = await self._decompose_level(
                    objective=goal.objective,
                    constraints=[c.description for c in goal.constraints],
                    parent_id=None,
                    depth=0,
                )
            except Exception as exc:
                raise TaskDecompositionError(f"Task decomposition failed: {exc}") from exc

            if not tasks:
                raise TaskDecompositionError(
                    "Decomposition produced zero tasks for the given goal."
                )

            self._log.info(
                "goal_decomposed", objective_len=len(goal.objective), tasks=len(tasks)
            )
            return tasks

    # ------------------------------------------------------------------
    # Recursive decomposition
    # ------------------------------------------------------------------

    async def _decompose_level(
        self,
        *,
        objective: str,
        constraints: list[str],
        parent_id: str | None,
        depth: int,
    ) -> tuple[list[Task], dict[str, str]]:
        raw_tasks = await self._llm_decompose(objective, constraints)

        tasks: list[Task] = []
        name_to_id: dict[str, str] = {}

        for raw in raw_tasks:
            task = Task(
                name=raw.get("name", "Untitled Task"),
                description=raw.get("description", ""),
                task_type=self._safe_task_type(raw.get("task_type")),
                parent_id=parent_id,
                required_capabilities=raw.get("required_capabilities", []) or [],
                metadata={"depends_on_names": raw.get("depends_on", []) or []},
            )
            name_to_id[task.name] = task.id
            tasks.append(task)

            if depth < self._max_depth and self._looks_compound(task.description):
                sub_tasks, sub_name_map = await self._decompose_level(
                    objective=task.description,
                    constraints=constraints,
                    parent_id=task.id,
                    depth=depth + 1,
                )
                tasks.extend(sub_tasks)
                name_to_id.update(sub_name_map)

        return tasks, name_to_id

    async def _llm_decompose(self, objective: str, constraints: list[str]) -> list[dict]:
        prompt = (
            _DECOMPOSE_INSTRUCTIONS
            .replace("{objective}", objective)
            .replace("{constraints}", "; ".join(constraints) or "none")
            .replace("{max_tasks}", str(self._max_tasks))
        )
        request = ChatRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            model_id=self._model_id or "",
            config=GenerationConfig(temperature=0.2, max_tokens=1200),
            timeout_seconds=45.0,
        )
        routing_ctx = None
        if not self._model_id:
            routing_ctx = RoutingContext(
                required_capabilities=[ModelCapability.TEXT_GENERATION]
            )
        response = await self._router.chat(request, routing_context=routing_ctx)
        content = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", response.content.strip(), flags=re.MULTILINE
        ).strip()
        data = json.loads(content)
        return data.get("tasks", [])[: self._max_tasks]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_task_type(value: str | None) -> TaskType:
        try:
            return TaskType(value) if value else TaskType.GENERIC
        except ValueError:
            return TaskType.GENERIC

    @staticmethod
    def _looks_compound(description: str) -> bool:
        """Heuristic: description likely contains multiple sub-actions."""
        lowered = description.lower()
        conjunction_count = lowered.count(" and ") + lowered.count(" then ")
        return conjunction_count >= 2 and len(description) > 200