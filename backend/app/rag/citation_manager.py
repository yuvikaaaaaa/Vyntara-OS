"""IOS RAG — Citation Manager."""
from __future__ import annotations

import re

from app.rag.base import BaseRAGComponent
from app.rag.exceptions import CitationError
from app.rag.interfaces import ICitationManager
from app.rag.types import Citation, CitedResponse, ClaimVerdict, GroundingResult
from app.retrieval.types import BuiltContext

# Matches inline citation markers like [1], [2], [12] emitted by the LLM
# per PromptAssembler's citation instruction.
_CITATION_MARKER_PATTERN = re.compile(r"\[(\d+)\]")


class CitationManager(BaseRAGComponent, ICitationManager):
    """
    Manages evidence provenance for generated responses.

    Responsibilities:
    - Extract citation markers ([1], [2], ...) the LLM emitted inline
    - Resolve each marker to its corresponding context item via
      BuiltContext.citations (produced by the Retrieval Engine)
    - Deduplicate citations pointing to the same underlying source
    - Normalise citation labels to a consistent sequential order
    - Track per-citation confidence (inherited from the retrieval score)
    - Attach grounding-derived citations for claims lacking explicit
      inline markers when a GroundingResult is available
    """

    async def inject_citations(
        self,
        response_text: str,
        context: BuiltContext,
        grounding: GroundingResult | None = None,
    ) -> CitedResponse:
        async with self._span("inject_citations"):
            try:
                citation_by_label = self._index_context_citations(context)
                explicit_citations = self._extract_explicit(response_text, citation_by_label)

                inferred_citations: list[Citation] = []
                uncited_count = 0
                if grounding is not None:
                    inferred_citations, uncited_count = self._infer_from_grounding(
                        grounding, context, existing_ids={c.item_id for c in explicit_citations}
                    )

                all_citations = self._deduplicate(explicit_citations + inferred_citations)
                normalised_text = self._renumber_markers(response_text, all_citations)

                self._log.info(
                    "citations_injected",
                    explicit=len(explicit_citations),
                    inferred=len(inferred_citations),
                    total=len(all_citations),
                    uncited_claims=uncited_count,
                )
                return CitedResponse(
                    text=normalised_text,
                    citations=all_citations,
                    uncited_claim_count=uncited_count,
                )
            except Exception as exc:
                raise CitationError(f"Citation injection failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _index_context_citations(
        self, context: BuiltContext
    ) -> dict[str, dict]:
        """Map citation label (e.g. '[1]') to its source metadata dict."""
        return {c["label"]: c for c in context.citations}

    def _extract_explicit(
        self, response_text: str, citation_by_label: dict[str, dict]
    ) -> list[Citation]:
        """Extract citations from inline [N] markers the LLM produced."""
        citations: list[Citation] = []
        seen_labels: set[str] = set()

        for match in _CITATION_MARKER_PATTERN.finditer(response_text):
            label = f"[{match.group(1)}]"
            if label in seen_labels:
                continue
            meta = citation_by_label.get(label)
            if meta is None:
                # LLM fabricated a citation number not present in context —
                # skip silently here; HallucinationChecker flags this case.
                continue
            seen_labels.add(label)
            citations.append(
                Citation(
                    label=label,
                    item_id=meta["id"],
                    source=meta["source"],
                    title=meta.get("title"),
                    parent_id=meta.get("parent_id"),
                    confidence=meta.get("confidence", 1.0),
                )
            )
        return citations

    def _infer_from_grounding(
        self,
        grounding: GroundingResult,
        context: BuiltContext,
        *,
        existing_ids: set[str],
    ) -> tuple[list[Citation], int]:
        """
        Add citations for claims the grounding checker matched to evidence
        but the LLM failed to explicitly cite inline.
        """
        item_by_id = {ri.item.id: ri for ri in context.included_items}
        citation_by_id = {c["id"]: c for c in context.citations}

        inferred: list[Citation] = []
        uncited_count = 0
        seen_ids = set(existing_ids)

        for evidence in grounding.claims:
            if evidence.verdict != ClaimVerdict.SUPPORTED:
                uncited_count += 1
                continue
            for item_id in evidence.supporting_item_ids:
                if item_id in seen_ids:
                    continue
                meta = citation_by_id.get(item_id)
                ri = item_by_id.get(item_id)
                if meta is None or ri is None:
                    continue
                seen_ids.add(item_id)
                inferred.append(
                    Citation(
                        label=meta["label"],
                        item_id=item_id,
                        source=meta["source"],
                        title=meta.get("title"),
                        parent_id=meta.get("parent_id"),
                        confidence=evidence.confidence,
                        cited_span=evidence.claim.text,
                    )
                )
        return inferred, uncited_count

    # ------------------------------------------------------------------
    # Deduplication / normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _deduplicate(citations: list[Citation]) -> list[Citation]:
        """Keep the highest-confidence citation per unique item_id."""
        best: dict[str, Citation] = {}
        for c in citations:
            existing = best.get(c.item_id)
            if existing is None or c.confidence > existing.confidence:
                best[c.item_id] = c
        return list(best.values())

    @staticmethod
    def _renumber_markers(text: str, citations: list[Citation]) -> str:
        """
        Re-map citation labels to a sequential [1], [2], ... order based on
        first appearance, keeping the response text's readability high even
        if the underlying context ordinal numbers were sparse or out of order.
        """
        if not citations:
            return text

        old_to_new: dict[str, str] = {}
        for idx, c in enumerate(citations, start=1):
            old_to_new[c.label] = f"[{idx}]"
            c.label = f"[{idx}]"

        def _replace(match: re.Match) -> str:
            old_label = f"[{match.group(1)}]"
            return old_to_new.get(old_label, match.group(0))

        return _CITATION_MARKER_PATTERN.sub(_replace, text)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def format_reference_list(citations: list[Citation]) -> str:
        """Render a human-readable reference list appendix for the citations."""
        if not citations:
            return ""
        lines = ["", "---", "**Sources:**"]
        for c in sorted(citations, key=lambda x: x.label):
            title = c.title or c.item_id
            lines.append(f"{c.label} {title} ({c.source})")
        return "\n".join(lines)