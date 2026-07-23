"""IOS Planner — Plan Optimizer."""
from __future__ import annotations

from copy import deepcopy

from app.planner.base import BasePlanner
from app.planner.dependency_graph import DependencyGraph
from app.planner.exceptions import PlanOptimizationError
from app.planner.interfaces import IPlanOptimizer
from app.planner.types import ExecutionPlan, Task

# Tasks below this estimated duration are candidates for merging with an
# adjacent low-cost task on the same critical-path batch, reducing
# orchestration overhead without changing dependency semantics.
_MERGE_DURATION_THRESHOLD_MS = 2_000


class PlanOptimizer(BasePlanner, IPlanOptimizer):
    """
    Optimizes an ExecutionPlan for latency, parallelism, and resource
    utilization while strictly preserving plan semantics.

    Optimizations applied:
    1. **Parallel batching** — recompute dependency batches via
       DependencyGraph.parallel_batches() so independent tasks are
       maximally grouped for concurrent execution.
    2. **Priority-aware batch ordering** — within a batch, order tasks by
       descending TaskPriority so critical work is dispatched first when
       a worker pool has limited concurrency.
    3. **Critical-path annotation** — tag tasks on the critical path in
       metadata so downstream execution can prioritise scheduling them
       promptly (never delayed behind non-critical work in the same batch).
    4. **Redundancy pruning** — remove duplicate dependency edges that add
       no new ordering constraint (a -> b -> c also implies a -> c
       transitively; an explicit a -> c edge is redundant and only adds
       graph-traversal overhead).

    The optimizer never adds, removes, or reorders tasks in a way that
    would change *what* gets executed or violate any dependency — only
    *how efficiently* the already-valid plan is scheduled.
    """

    def __init__(self, dependency_graph_factory=DependencyGraph) -> None:
        super().__init__()
        self._graph_factory = dependency_graph_factory

    async def optimize(self, plan: ExecutionPlan) -> ExecutionPlan:
        async with self._span("optimize_plan", tasks=str(len(plan.tasks))):
            start = self._now_ms()
            try:
                optimized = deepcopy(plan)

                graph = self._graph_factory()
                graph.build(optimized.tasks, optimized.dependencies)

                optimized.dependencies = self._prune_redundant_edges(
                    optimized.tasks, optimized.dependencies, graph
                )
                # Rebuild graph after pruning so batching reflects the
                # minimal edge set.
                graph.build(optimized.tasks, optimized.dependencies)

                batches = graph.parallel_batches()
                self._apply_batch_ordering(optimized.tasks, batches)

                critical_path = graph.critical_path()
                self._annotate_critical_path(optimized.tasks, critical_path)

                optimized.metadata.optimization_applied = True
                optimized.version += 1

                self._log.info(
                    "plan_optimized",
                    plan_id=optimized.id,
                    batches=len(batches),
                    critical_path_length=len(critical_path),
                    edges_before=len(plan.dependencies),
                    edges_after=len(optimized.dependencies),
                    latency_ms=self._elapsed_ms(start),
                )
                return optimized
            except Exception as exc:
                raise PlanOptimizationError(f"Plan optimization failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Redundant edge pruning (transitive reduction, simplified)
    # ------------------------------------------------------------------

    def _prune_redundant_edges(
        self,
        tasks: list[Task],
        dependencies: list,
        graph: DependencyGraph,
    ) -> list:
        """
        Remove dependency edges (a -> c) that are already implied by a
        longer existing path (a -> b -> c), reducing graph complexity
        without changing the resulting execution ordering constraints.
        """
        pruned: list = []
        for dep in dependencies:
            others = [
                d for d in dependencies
                if d is not dep and d.task_id == dep.task_id
            ]
            if self._is_transitively_implied(dep, others, graph):
                continue
            pruned.append(dep)
        return pruned

    def _is_transitively_implied(self, dep, other_deps: list, graph: DependencyGraph) -> bool:
        """
        Check whether dep.depends_on_id is reachable from dep.task_id via
        any *other* direct dependency, at more than one hop (i.e. the
        direct edge is a shortcut that adds no new constraint).
        """
        target = dep.depends_on_id
        visited: set[str] = set()
        stack = [d.depends_on_id for d in other_deps]

        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in visited:
                continue
            visited.add(current)
            stack.extend(graph.get_dependencies(current))
        return False

    # ------------------------------------------------------------------
    # Batch ordering
    # ------------------------------------------------------------------

    @staticmethod
    def _apply_batch_ordering(tasks: list[Task], batches: list[list[str]]) -> None:
        """Annotate each task with its computed batch index and priority rank."""
        task_by_id = {t.id: t for t in tasks}
        for batch_index, batch in enumerate(batches):
            ordered = sorted(
                batch,
                key=lambda tid: task_by_id[tid].priority,
                reverse=True,
            )
            for rank, tid in enumerate(ordered):
                task = task_by_id[tid]
                task.metadata["batch_index"] = batch_index
                task.metadata["batch_rank"] = rank

    # ------------------------------------------------------------------
    # Critical path annotation
    # ------------------------------------------------------------------

    @staticmethod
    def _annotate_critical_path(tasks: list[Task], critical_path: list[str]) -> None:
        critical_set = set(critical_path)
        for task in tasks:
            task.metadata["on_critical_path"] = task.id in critical_set