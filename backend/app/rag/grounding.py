"""IOS RAG — Grounding."""
from __future__ import annotations

from app.rag.base import BaseRAGComponent
from app.rag.exceptions import GroundingError
from app.rag.interfaces import IGroundingChecker
from app.rag.types import (
    Claim,
    ClaimEvidence,
    ClaimVerdict,
    GroundingLevel,
    GroundingResult,
)
from app.retrieval.types import BuiltContext

# Token-overlap thresholds for heuristic claim verification.
_SUPPORTED_THRESHOLD = 0.30
_PARTIAL_THRESHOLD = 0.15


class Grounding(BaseRAGComponent, IGroundingChecker):
    """
    Verifies that generated claims are supported by retrieved context.

    Default implementation uses token-overlap similarity (Jaccard) between
    each claim and each context item as a fast, dependency-free heuristic.
    An optional semantic scorer (e.g. an AI Core embedding-based similarity
    function) can be injected to replace the heuristic with a stronger
    signal without changing the public contract.
    """

    def __init__(
        self,
        *,
        semantic_scorer=None,   # Optional async callable (claim_text, item_text) -> float
        supported_threshold: float = _SUPPORTED_THRESHOLD,
        partial_threshold: float = _PARTIAL_THRESHOLD,
    ) -> None:
        super().__init__()
        self._semantic_scorer = semantic_scorer
        self._supported_threshold = supported_threshold
        self._partial_threshold = partial_threshold

    async def check_grounding(
        self,
        response_text: str,
        context: BuiltContext,
    ) -> GroundingResult:
        async with self._span("check_grounding"):
            try:
                claims = self.split_claims(response_text)
                if not claims:
                    return GroundingResult(
                        level=GroundingLevel.UNGROUNDED,
                        overall_confidence=0.0,
                        grounding_ratio=0.0,
                    )

                evidence_list: list[ClaimEvidence] = []
                for claim in claims:
                    evidence = await self._evaluate_claim(claim, context)
                    evidence_list.append(evidence)

                supported = [e for e in evidence_list if e.verdict == ClaimVerdict.SUPPORTED]
                ungrounded_claims = [
                    e.claim for e in evidence_list if e.verdict != ClaimVerdict.SUPPORTED
                ]

                grounding_ratio = len(supported) / len(claims)
                overall_confidence = (
                    sum(e.confidence for e in evidence_list) / len(evidence_list)
                )
                level = self._classify_level(grounding_ratio)

                result = GroundingResult(
                    level=level,
                    overall_confidence=overall_confidence,
                    claims=evidence_list,
                    ungrounded_claims=ungrounded_claims,
                    grounding_ratio=grounding_ratio,
                )
                self._log.info(
                    "grounding_checked",
                    total_claims=len(claims),
                    supported=len(supported),
                    level=level.value,
                    ratio=round(grounding_ratio, 3),
                )
                return result
            except Exception as exc:
                raise GroundingError(f"Grounding check failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _evaluate_claim(
        self, claim: Claim, context: BuiltContext
    ) -> ClaimEvidence:
        if not context.included_items:
            return ClaimEvidence(
                claim=claim,
                verdict=ClaimVerdict.UNVERIFIABLE,
                confidence=0.0,
                explanation="No context items available for verification.",
            )

        best_score = 0.0
        best_item_ids: list[str] = []
        for ri in context.included_items:
            item_text = ri.item.content
            if self._semantic_scorer is not None:
                try:
                    score = await self._semantic_scorer(claim.text, item_text)
                except Exception as exc:
                    self._log.warning("semantic_scorer_failed", exc=str(exc))
                    score = self.token_overlap_ratio(claim.text, item_text)
            else:
                score = self.token_overlap_ratio(claim.text, item_text)

            if score > best_score:
                best_score = score
                best_item_ids = [ri.item.id]
            elif score == best_score and score > 0:
                best_item_ids.append(ri.item.id)

        verdict, confidence = self._score_to_verdict(best_score)
        return ClaimEvidence(
            claim=claim,
            verdict=verdict,
            supporting_item_ids=best_item_ids if verdict == ClaimVerdict.SUPPORTED else [],
            confidence=confidence,
            explanation=(
                f"Best overlap score {best_score:.2f} against retrieved context."
            ),
        )

    def _score_to_verdict(self, score: float) -> tuple[ClaimVerdict, float]:
        if score >= self._supported_threshold:
            return ClaimVerdict.SUPPORTED, min(1.0, score * 1.5)
        if score >= self._partial_threshold:
            return ClaimVerdict.UNVERIFIABLE, score
        return ClaimVerdict.UNSUPPORTED, score

    @staticmethod
    def _classify_level(ratio: float) -> GroundingLevel:
        if ratio >= 0.85:
            return GroundingLevel.FULLY_GROUNDED
        if ratio >= 0.40:
            return GroundingLevel.PARTIALLY_GROUNDED
        return GroundingLevel.UNGROUNDED