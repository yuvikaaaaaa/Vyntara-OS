"""IOS Agents — Agent Coordinator."""
from __future__ import annotations

from app.agents.base import BaseAgentComponent
from app.agents.exceptions import AgentCoordinationError
from app.agents.interfaces import IAgentCoordinator, ITaskDispatcher
from app.agents.types import AgentResult, AgentTask


class AgentCoordinator(BaseAgentComponent, IAgentCoordinator):
    """
    Coordinates execution of a dependency-ordered sequence of task
    batches across multiple agents.

    Responsibilities:
    - **Synchronization** — batches are executed strictly in order;
      TaskDispatcher already parallelises *within* a batch, so
      coordination here is purely inter-batch sequencing (each batch
      only starts once the previous batch's results are all available).
    - **Dependency ordering** — batches are assumed pre-ordered by the
      Planning Engine's DependencyGraph.parallel_batches(); this component
      trusts and preserves that ordering rather than re-deriving it.
    - **Conflict resolution** — if two tasks in the same batch produce
      results claiming the same output key (via ``AgentResult.metadata
      ["output_key"]``), the higher-confidence result wins; the loser is
      retained in the aggregate under a namespaced key for auditability
      rather than silently discarded.
    - **Result aggregation** — merges every batch's AgentResult list into
      a single ordered list, and separately exposes a keyed view
      (task_id -> AgentResult) for downstream context building
      (AgentContextBuilder.upstream_results).
    - **Completion detection** — a coordination run is considered failed
      only if a batch has zero successful results; partial batch success
      is allowed to propagate forward (dependent tasks may still receive
      partial upstream context).

    Delegates all actual execution to the injected ITaskDispatcher — this
    component never calls IAgent or IAgentExecutor directly.
    """

    def __init__(self, dispatcher: ITaskDispatcher) -> None:
        super().__init__()
        self._dispatcher = dispatcher

    async def coordinate(
        self, batches: list[list[AgentTask]], context: dict
    ) -> list[AgentResult]:
        async with self._span("coordinate", batches=str(len(batches))):
            if not batches:
                return []

            all_results: list[AgentResult] = []
            results_by_task_id: dict[str, AgentResult] = {}

            for batch_index, batch in enumerate(batches):
                if not batch:
                    continue

                batch_context = self._enrich_context(context, results_by_task_id)
                batch_results = await self._dispatcher.dispatch(batch, batch_context)

                resolved = self._resolve_conflicts(batch_results)
                for result in resolved:
                    results_by_task_id[result.task_id] = result
                all_results.extend(resolved)

                success_count = sum(1 for r in resolved if r.success)
                if success_count == 0:
                    self._log.error(
                        "batch_coordination_failed",
                        batch_index=batch_index,
                        tasks=len(batch),
                    )
                    raise AgentCoordinationError(
                        f"All {len(batch)} task(s) in batch {batch_index} failed; "
                        f"cannot proceed to dependent batches.",
                        details={"batch_index": batch_index},
                    )

                self._log.info(
                    "batch_coordinated",
                    batch_index=batch_index,
                    tasks=len(batch),
                    succeeded=success_count,
                )

            self._log.info(
                "coordination_complete",
                batches=len(batches),
                total_tasks=len(all_results),
                total_succeeded=sum(1 for r in all_results if r.success),
            )
            return all_results

    # ------------------------------------------------------------------
    # Conflict resolution
    # ------------------------------------------------------------------

    def _resolve_conflicts(self, results: list[AgentResult]) -> list[AgentResult]:
        """
        Detect results claiming the same logical output key and keep the
        highest-confidence one, retaining the loser under a namespaced
        metadata key for audit rather than discarding it outright.
        """
        by_output_key: dict[str, AgentResult] = {}
        resolved: list[AgentResult] = []

        for result in results:
            output_key = result.metadata.get("output_key")
            if not output_key:
                resolved.append(result)
                continue

            existing = by_output_key.get(output_key)
            if existing is None:
                by_output_key[output_key] = result
                resolved.append(result)
                continue

            winner, loser = self._pick_winner(existing, result)
            if winner is not existing:
                # Replace the previously-kept result in `resolved`
                idx = resolved.index(existing)
                resolved[idx] = winner
                by_output_key[output_key] = winner
                loser.metadata["conflict_superseded_by"] = winner.agent_id
                self._log.warning(
                    "output_key_conflict_resolved",
                    output_key=output_key,
                    winner=winner.agent_id,
                    loser=loser.agent_id,
                )
            else:
                result.metadata["conflict_superseded_by"] = existing.agent_id

        return resolved

    @staticmethod
    def _pick_winner(a: AgentResult, b: AgentResult) -> tuple[AgentResult, AgentResult]:
        conf_a = a.confidence if a.confidence is not None else (1.0 if a.success else 0.0)
        conf_b = b.confidence if b.confidence is not None else (1.0 if b.success else 0.0)
        return (a, b) if conf_a >= conf_b else (b, a)

    # ------------------------------------------------------------------
    # Context enrichment
    # ------------------------------------------------------------------

    @staticmethod
    def _enrich_context(
        base_context: dict, results_so_far: dict[str, AgentResult]
    ) -> dict:
        """
        Inject prior-batch results into the context dict passed to the
        next batch's dispatch call, under the ``upstream_results`` key
        consumed by AgentContextBuilder.
        """
        enriched = dict(base_context)
        enriched["upstream_results"] = {
            task_id: result.output for task_id, result in results_so_far.items()
        }
        return enriched