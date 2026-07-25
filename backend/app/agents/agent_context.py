"""IOS Agents — Agent Context Builder."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.agents.base import BaseAgentComponent
from app.agents.types import AgentTask


@dataclass
class ExecutionContext:
    """
    The merged execution context handed to every agent invocation.

    Composed from up to four independent sources, each optional so the
    context builder degrades gracefully when a source is unavailable
    (e.g. no retrieval results, no prior memory).
    """
    task: AgentTask
    user_id: UUID | None = None
    conversation_id: UUID | None = None
    plan_metadata: dict[str, Any] = field(default_factory=dict)
    memory_context: str | None = None
    retrieval_context: str | None = None
    retrieval_citations: list[dict] = field(default_factory=list)
    user_preferences: dict[str, Any] = field(default_factory=dict)
    upstream_results: dict[str, Any] = field(default_factory=dict)   # task_id -> AgentResult.output
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Flatten to a plain dict for passing into IAgent.execute(context)."""
        return {
            "task_id": self.task.id,
            "task_name": self.task.name,
            "task_description": self.task.description,
            "task_inputs": self.task.inputs,
            "user_id": str(self.user_id) if self.user_id else None,
            "conversation_id": str(self.conversation_id) if self.conversation_id else None,
            "plan_metadata": self.plan_metadata,
            "memory_context": self.memory_context,
            "retrieval_context": self.retrieval_context,
            "retrieval_citations": self.retrieval_citations,
            "user_preferences": self.user_preferences,
            "upstream_results": self.upstream_results,
            **self.extra,
        }


class AgentContextBuilder(BaseAgentComponent):
    """
    Assembles the ExecutionContext for a task by merging multiple sources.

    Sources are injected as loosely-typed callables/gateways to avoid
    hard dependencies on concrete Memory/Retrieval implementations —
    the Agent Engine communicates with those layers only through
    whatever interface object is supplied here (duck-typed, matching
    the Memory Engine's MemoryManager and Retrieval Engine's
    RetrievalManager public APIs).
    """

    def __init__(
        self,
        *,
        memory_gateway=None,     # object exposing async export_working_context()
        retrieval_gateway=None,  # object exposing async retrieve_with_context(request)
    ) -> None:
        super().__init__()
        self._memory_gateway = memory_gateway
        self._retrieval_gateway = retrieval_gateway

    async def build(
        self,
        task: AgentTask,
        *,
        user_id: UUID | None = None,
        conversation_id: UUID | None = None,
        plan_metadata: dict[str, Any] | None = None,
        user_preferences: dict[str, Any] | None = None,
        upstream_results: dict[str, Any] | None = None,
        query_for_retrieval: str | None = None,
    ) -> ExecutionContext:
        async with self._span("build_context", task_id=task.id):
            memory_context = await self._fetch_memory_context()
            retrieval_context, citations = await self._fetch_retrieval_context(
                query_for_retrieval or task.description, user_id
            )

            context = ExecutionContext(
                task=task,
                user_id=user_id,
                conversation_id=conversation_id,
                plan_metadata=plan_metadata or {},
                memory_context=memory_context,
                retrieval_context=retrieval_context,
                retrieval_citations=citations,
                user_preferences=user_preferences or {},
                upstream_results=upstream_results or {},
            )
            self._log.info(
                "execution_context_built",
                task_id=task.id,
                has_memory=memory_context is not None,
                has_retrieval=retrieval_context is not None,
                upstream_count=len(context.upstream_results),
            )
            return context

    # ------------------------------------------------------------------
    # Source fetchers (each fails soft — never blocks context assembly)
    # ------------------------------------------------------------------

    async def _fetch_memory_context(self) -> str | None:
        if self._memory_gateway is None:
            return None
        try:
            return await self._memory_gateway.export_working_context()
        except Exception as exc:
            self._log.warning("memory_context_fetch_failed", exc=str(exc))
            return None

    async def _fetch_retrieval_context(
        self, query: str, user_id: UUID | None
    ) -> tuple[str | None, list[dict]]:
        if self._retrieval_gateway is None or user_id is None or not query:
            return None, []
        try:
            from app.retrieval.types import RetrievalRequest

            request = RetrievalRequest(query=query, user_id=user_id, top_k=8)
            _, built_context = await self._retrieval_gateway.retrieve_with_context(request)
            return built_context.text or None, built_context.citations
        except Exception as exc:
            self._log.warning("retrieval_context_fetch_failed", exc=str(exc))
            return None, []