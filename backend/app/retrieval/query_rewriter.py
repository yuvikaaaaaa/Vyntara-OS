"""IOS Retrieval — Query Rewriter."""
from __future__ import annotations

from app.ai_core.router.model_router import ModelRouter
from app.ai_core.types import ChatMessage, ChatRequest, GenerationConfig, ModelCapability, RoutingContext
from app.core.logging import get_logger
from app.core.telemetry import create_async_span
from app.retrieval.interfaces import IQueryRewriter
from app.retrieval.types import QueryRewriteResult, QueryRewriteStrategy

logger = get_logger(__name__)


class QueryRewriter(IQueryRewriter):
    """
    LLM-assisted query transformation for improved retrieval recall.

    Strategies:
    - NONE: pass-through, no LLM call
    - EXPANSION: append synonyms / related terms to broaden recall
    - DECOMPOSITION: split a complex query into independent sub-queries
    - HYDE: generate a hypothetical answer document, embedded instead of
            the raw query (Hypothetical Document Embeddings technique)

    Falls back to the original query on any LLM failure — a rewrite
    failure must never block retrieval.
    """

    def __init__(
        self,
        model_router: ModelRouter,
        *,
        model_id: str | None = None,
        model_tier_hint: str = "small",
    ) -> None:
        self._router = model_router
        self._model_id = model_id
        self._tier_hint = model_tier_hint
        self._log = logger

    async def rewrite(
        self, query: str, strategy: QueryRewriteStrategy
    ) -> QueryRewriteResult:
        async with create_async_span(
            "retrieval.query_rewrite", attributes={"strategy": strategy.value}
        ):
            if strategy == QueryRewriteStrategy.NONE:
                return QueryRewriteResult(
                    original_query=query, rewritten_query=query, strategy=strategy
                )
            try:
                if strategy == QueryRewriteStrategy.EXPANSION:
                    return await self._expand(query)
                if strategy == QueryRewriteStrategy.DECOMPOSITION:
                    return await self._decompose(query)
                if strategy == QueryRewriteStrategy.HYDE:
                    return await self._hyde(query)
            except Exception as exc:
                self._log.warning(
                    "query_rewrite_failed", strategy=strategy.value, exc=str(exc)
                )
            return QueryRewriteResult(
                original_query=query, rewritten_query=query, strategy=QueryRewriteStrategy.NONE
            )

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    async def _expand(self, query: str) -> QueryRewriteResult:
        prompt = (
            "Expand the following search query with relevant synonyms and "
            "related terms to improve retrieval recall. Return ONLY the "
            f"expanded query text, no explanation.\n\nQuery: {query}"
        )
        expanded = await self._complete(prompt, max_tokens=150)
        return QueryRewriteResult(
            original_query=query,
            rewritten_query=expanded.strip() or query,
            strategy=QueryRewriteStrategy.EXPANSION,
        )

    async def _decompose(self, query: str) -> QueryRewriteResult:
        prompt = (
            "Decompose the following complex query into 2-4 independent, "
            "self-contained sub-questions. Return ONLY the sub-questions, "
            f"one per line, no numbering or explanation.\n\nQuery: {query}"
        )
        raw = await self._complete(prompt, max_tokens=300)
        sub_queries = [line.strip("- ").strip() for line in raw.splitlines() if line.strip()]
        return QueryRewriteResult(
            original_query=query,
            rewritten_query=query,
            sub_queries=sub_queries or [query],
            strategy=QueryRewriteStrategy.DECOMPOSITION,
        )

    async def _hyde(self, query: str) -> QueryRewriteResult:
        prompt = (
            "Write a short, plausible passage (3-5 sentences) that would "
            "directly answer the following question, as if it came from an "
            f"authoritative document. Do not mention that this is hypothetical.\n\nQuestion: {query}"
        )
        hypothetical = await self._complete(prompt, max_tokens=250)
        return QueryRewriteResult(
            original_query=query,
            rewritten_query=hypothetical.strip() or query,
            hypothetical_document=hypothetical.strip() or None,
            strategy=QueryRewriteStrategy.HYDE,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _complete(self, prompt: str, *, max_tokens: int) -> str:
        request = ChatRequest(
            messages=[ChatMessage(role="user", content=prompt)],
            model_id=self._model_id or "",
            config=GenerationConfig(temperature=0.3, max_tokens=max_tokens),
            timeout_seconds=30.0,
        )
        routing_ctx = None
        if not self._model_id:
            from app.ai_core.types import ModelTierLabel
            tier = ModelTierLabel(self._tier_hint) if self._tier_hint in ModelTierLabel._value2member_map_ else None
            routing_ctx = RoutingContext(
                required_capabilities=[ModelCapability.TEXT_GENERATION],
                preferred_tier=tier,
            )
        response = await self._router.chat(request, routing_context=routing_ctx)
        return response.content