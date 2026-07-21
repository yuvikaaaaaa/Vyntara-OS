"""IOS Planner — Dependency Graph."""
from __future__ import annotations

from collections import defaultdict, deque

from app.planner.base import BasePlanner
from app.planner.exceptions import DependencyCycleError
from app.planner.interfaces import IDependencyGraph
from app.planner.types import Task, TaskDependency


class DependencyGraph(BasePlanner, IDependencyGraph):
    """
    Builds and validates a directed acyclic graph (DAG) of task
    dependencies.

    Implements:
    - Adjacency-list graph construction from Task + TaskDependency lists
    - Cycle detection via DFS with a recursion stack (Tarjan-style colouring)
    - Topological ordering via Kahn's algorithm (BFS on in-degree)
    - Critical path computation via longest-path DP over the DAG, weighted
      by each task's estimated_duration_ms (defaults to 1 "unit" per task
      when no duration estimate is available)

    A single DependencyGraph instance is built once via build() and then
    queried; it is not safe to mutate tasks/dependencies after construction
    without calling build() again.
    """

    def __init__(self) -> None:
        super().__init__()
        self._tasks: dict[str, Task] = {}
        self._edges: dict[str, set[str]] = defaultdict(set)          # id -> depends_on ids
        self._reverse_edges: dict[str, set[str]] = defaultdict(set)  # id -> dependents

    def build(self, tasks: list[Task], dependencies: list[TaskDependency]) -> None:
        self._tasks = {t.id: t for t in tasks}
        self._edges = defaultdict(set)
        self._reverse_edges = defaultdict(set)

        for dep in dependencies:
            if dep.task_id not in self._tasks or dep.depends_on_id not in self._tasks:
                continue  # dangling reference — surfaced by ConstraintSolver, not here
            self._edges[dep.task_id].add(dep.depends_on_id)
            self._reverse_edges[dep.depends_on_id].add(dep.task_id)

        self._log.info(
            "dependency_graph_built",
            tasks=len(self._tasks),
            edges=sum(len(v) for v in self._edges.values()),
        )

    def topological_order(self) -> list[str]:
        """
        Kahn's algorithm: repeatedly remove nodes with zero remaining
        in-degree (i.e. all their dependencies have been "resolved").

        Raises:
            DependencyCycleError: If a cycle prevents full ordering.
        """
        in_degree: dict[str, int] = {tid: len(self._edges[tid]) for tid in self._tasks}
        queue: deque[str] = deque(
            sorted(tid for tid, deg in in_degree.items() if deg == 0)
        )
        order: list[str] = []

        while queue:
            current = queue.popleft()
            order.append(current)
            for dependent in sorted(self._reverse_edges[current]):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(order) != len(self._tasks):
            cycles = self.detect_cycles()
            raise DependencyCycleError(
                "Dependency graph contains a cycle; cannot produce a valid "
                "topological order.",
                details={"cycles": cycles},
            )
        return order

    def detect_cycles(self) -> list[list[str]]:
        """
        DFS-based cycle detection using node colouring:
        WHITE (unvisited) -> GRAY (in current DFS stack) -> BLACK (done).

        Returns:
            List of cycles, each represented as the ordered list of task
            ids forming the cycle.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        colour: dict[str, int] = {tid: WHITE for tid in self._tasks}
        cycles: list[list[str]] = []
        path: list[str] = []

        def dfs(node: str) -> None:
            colour[node] = GRAY
            path.append(node)
            for neighbour in self._edges[node]:
                if colour[neighbour] == GRAY:
                    cycle_start = path.index(neighbour)
                    cycles.append(path[cycle_start:] + [neighbour])
                elif colour[neighbour] == WHITE:
                    dfs(neighbour)
            path.pop()
            colour[node] = BLACK

        for tid in self._tasks:
            if colour[tid] == WHITE:
                dfs(tid)
        return cycles

    def critical_path(self) -> list[str]:
        """
        Longest-path DP over the DAG, weighted by estimated_duration_ms
        (or 1 if unavailable), computed in reverse topological order.

        Returns:
            Task id sequence forming the critical (longest) path, empty
            list if the graph has a cycle (call detect_cycles() first in
            that case).
        """
        try:
            order = self.topological_order()
        except DependencyCycleError:
            return []

        # dist[t] = longest path duration ending at t
        # pred[t] = predecessor on that longest path
        dist: dict[str, int] = {tid: self._weight(tid) for tid in self._tasks}
        pred: dict[str, str | None] = {tid: None for tid in self._tasks}

        for tid in order:
            for dependent in self._reverse_edges[tid]:
                candidate = dist[tid] + self._weight(dependent)
                if candidate > dist[dependent]:
                    dist[dependent] = candidate
                    pred[dependent] = tid

        if not dist:
            return []
        end_node = max(dist, key=lambda t: dist[t])
        path: list[str] = []
        current: str | None = end_node
        while current is not None:
            path.append(current)
            current = pred[current]
        path.reverse()
        return path

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_dependencies(self, task_id: str) -> set[str]:
        """Return the set of task ids that task_id directly depends on."""
        return set(self._edges.get(task_id, set()))

    def get_dependents(self, task_id: str) -> set[str]:
        """Return the set of task ids that directly depend on task_id."""
        return set(self._reverse_edges.get(task_id, set()))

    def parallel_batches(self) -> list[list[str]]:
        """
        Group tasks into ordered batches where every task in a batch has
        all its dependencies satisfied by tasks in strictly earlier
        batches — i.e. every batch's tasks are safe to execute in parallel.
        """
        order = self.topological_order()
        batch_index: dict[str, int] = {}

        for tid in order:
            deps = self._edges[tid]
            batch_index[tid] = (
                max((batch_index[d] for d in deps), default=-1) + 1
            )

        max_batch = max(batch_index.values(), default=-1)
        batches: list[list[str]] = [[] for _ in range(max_batch + 1)]
        for tid, idx in batch_index.items():
            batches[idx].append(tid)
        return batches

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _weight(self, task_id: str) -> int:
        task = self._tasks.get(task_id)
        if task and task.estimated_duration_ms:
            return task.estimated_duration_ms
        return 1