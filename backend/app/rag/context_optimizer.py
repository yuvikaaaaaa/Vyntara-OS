"""IOS RAG — Context Optimizer."""
from __future__ import annotations

from app.rag.base import BaseRAGComponent
from app.rag.exceptions import ContextOptimizationError
from app.rag.interfaces import IContextOptimizer
from app.rag.types import CompressionStrategy
from app.retrieval.types import ContextBudget, RerankedItem

# Two items whose token-overlap similarity exceeds this threshold are
# considered redundant; the lower-ranked one is dropped.
_REDUNDANCY_THRESHOLD = 0.75


class ContextOptimizer(BaseRAGComponent, IContextOptimizer):
    """
    Compresses and de-redundantly filters retrieved context before it
    reaches PromptAssembler, maximising semantic coverage per token spent.

    Strategies (app.rag.types.CompressionStrategy):
    - NONE: pass-through, no optimisation
    - DEDUPLICATE_ONLY: drop near-duplicate items (default)
    - TRUNCATE: deduplicate, then hard-truncate each item to a per-item
                token cap
    - EXTRACTIVE_SUMMARY: deduplicate, then keep only the most query-relevant
                sentences from each item (sentence-selection based, no LLM
                call required)

    Citation integrity is preserved throughout: item ``id`` and
    ``parent_id`` are never altered by any strategy, only ``content`` may
    be shortened.
    """

    def __init__(
        self,
        *,
        redundancy_threshold: float = _REDUNDANCY_THRESHOLD,
        default_strategy: CompressionStrategy = CompressionStrategy.DEDUPLICATE_ONLY,
    ) -> None:
        super().__init__()
        self._redundancy_threshold = redundancy_threshold
        self._default_strategy = default_strategy

    async def optimize(
        self,
        items: list[RerankedItem],
        budget: ContextBudget,
        query: str,
    ) -> list[RerankedItem]:
        return await self.optimize_with_strategy(
            items, budget, query, self._default_strategy
        )

    async def optimize_with_strategy(
        self,
        items: list[RerankedItem],
        budget: ContextBudget,
        query: str,
        strategy: CompressionStrategy,
    ) -> list[RerankedItem]:
        async with self._span("optimize", strategy=strategy.value, candidates=str(len(items))):
            try:
                if not items:
                    return []

                if strategy == CompressionStrategy.NONE:
                    return items

                deduped = self._remove_redundant(items)

                if strategy == CompressionStrategy.DEDUPLICATE_ONLY:
                    result = deduped
                elif strategy == CompressionStrategy.TRUNCATE:
                    result = self._truncate_items(deduped, budget)
                elif strategy == CompressionStrategy.EXTRACTIVE_SUMMARY:
                    result = self._extractive_summarise(deduped, query, budget)
                else:
                    result = deduped

                self._log.info(
                    "context_optimized",
                    strategy=strategy.value,
                    before=len(items),
                    after=len(result),
                )
                return result
            except Exception as exc:
                raise ContextOptimizationError(f"Context optimization failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Redundancy removal
    # ------------------------------------------------------------------

    def _remove_redundant(self, items: list[RerankedItem]) -> list[RerankedItem]:
        """
        Greedy redundancy removal: process items in rank order, drop any
        item whose content substantially overlaps with an already-kept
        higher-ranked item.
        """
        ordered = sorted(items, key=lambda ri: ri.rerank_score, reverse=True)
        kept: list[RerankedItem] = []

        for candidate in ordered:
            is_redundant = False
            for existing in kept:
                overlap = self.token_overlap_ratio(
                    candidate.item.content, existing.item.content
                )
                if overlap >= self._redundancy_threshold:
                    is_redundant = True
                    break
            if not is_redundant:
                kept.append(candidate)
        return kept

    # ------------------------------------------------------------------
    # Truncation strategy
    # ------------------------------------------------------------------

    def _truncate_items(
        self, items: list[RerankedItem], budget: ContextBudget
    ) -> list[RerankedItem]:
        per_item_cap = budget.per_item_max_tokens or (
            budget.usable_tokens // max(len(items), 1)
        )
        max_chars = int(per_item_cap * self.CHARS_PER_TOKEN)

        result: list[RerankedItem] = []
        for ri in items:
            content = ri.item.content
            if self.estimate_tokens(content) > per_item_cap:
                truncated = content[:max_chars].rsplit(" ", 1)[0] + "…"
                ri.item.content = truncated
            result.append(ri)
        return result

    # ------------------------------------------------------------------
    # Extractive summarisation strategy
    # ------------------------------------------------------------------

    def _extractive_summarise(
        self,
        items: list[RerankedItem],
        query: str,
        budget: ContextBudget,
    ) -> list[RerankedItem]:
        """
        For each item, keep only the sentences most relevant to the query
        (by token-overlap score), preserving semantic coverage while
        cutting token cost — no LLM call required.
        """
        query_terms_present = bool(query.strip())
        per_item_cap = budget.per_item_max_tokens or max(
            50, budget.usable_tokens // max(len(items), 1)
        )

        result: list[RerankedItem] = []
        for ri in items:
            sentences = self._split_sentences(ri.item.content)
            if not sentences:
                result.append(ri)
                continue

            if query_terms_present:
                scored = sorted(
                    sentences,
                    key=lambda s: self.token_overlap_ratio(query, s),
                    reverse=True,
                )
            else:
                scored = sentences

            selected: list[str] = []
            tokens_used = 0
            for sentence in scored:
                sentence_tokens = self.estimate_tokens(sentence)
                if tokens_used + sentence_tokens > per_item_cap:
                    continue
                selected.append(sentence)
                tokens_used += sentence_tokens

            if selected:
                # Restore original sentence order for readability
                ordered_selected = [s for s in sentences if s in selected]
                ri.item.content = " ".join(ordered_selected)
            result.append(ri)
        return result

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        import re
        normalised = re.sub(r"\s+", " ", text).strip()
        if not normalised:
            return []
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\u2018\u201c])", normalised)
        return [p.strip() for p in parts if p.strip()]