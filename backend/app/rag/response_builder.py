"""IOS RAG — Response Builder."""
from __future__ import annotations

from app.ai_core.types import ChatResponse
from app.rag.base import BaseRAGComponent
from app.rag.exceptions import ResponseBuildError
from app.rag.types import (
    CitedResponse,
    GroundingLevel,
    GroundingResult,
    HallucinationReport,
    RAGRequest,
    RAGResponse,
    ValidationResult,
)


class ResponseBuilder(BaseRAGComponent):
    """
    Constructs the final, immutable RAGResponse from the outputs of every
    pipeline stage.

    This component performs no analysis of its own — it is a pure
    assembly step that preserves every piece of information produced
    upstream (generation, citations, grounding, hallucination report,
    validation) into a single structured object for the caller.
    """

    def build(
        self,
        *,
        request: RAGRequest,
        chat_response: ChatResponse,
        cited_response: CitedResponse,
        grounding: GroundingResult | None,
        hallucination: HallucinationReport | None,
        validation: ValidationResult | None,
        latency_ms: int,
        context_items_used: int,
    ) -> RAGResponse:
        """
        Assemble the final RAGResponse.

        Raises:
            ResponseBuildError: On unexpected assembly failure.
        """
        try:
            confidence = self._compute_confidence(grounding, hallucination, validation)

            response = RAGResponse(
                query=request.query,
                answer=cited_response.text,
                citations=cited_response.citations,
                grounding=grounding,
                hallucination=hallucination,
                validation=validation,
                model_id=chat_response.model_id,
                prompt_tokens=chat_response.usage.prompt_tokens,
                completion_tokens=chat_response.usage.completion_tokens,
                total_tokens=chat_response.usage.total_tokens,
                latency_ms=latency_ms,
                context_items_used=context_items_used,
                confidence_score=confidence,
            )
            self._log.info(
                "rag_response_built",
                model=chat_response.model_id,
                tokens=chat_response.usage.total_tokens,
                confidence=round(confidence, 3),
                citations=len(cited_response.citations),
                latency_ms=latency_ms,
            )
            return response
        except Exception as exc:
            raise ResponseBuildError(f"Failed to build RAG response: {exc}") from exc

    def build_error_response(
        self,
        *,
        request: RAGRequest,
        error_message: str,
        latency_ms: int = 0,
    ) -> RAGResponse:
        """
        Build a degraded-but-valid RAGResponse when the pipeline cannot
        complete normally (e.g. no context available, generation failure).

        Ensures callers always receive a well-formed RAGResponse object
        rather than needing to special-case exceptions for every failure
        mode.
        """
        return RAGResponse(
            query=request.query,
            answer=error_message,
            citations=[],
            grounding=None,
            hallucination=None,
            validation=None,
            model_id="",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            latency_ms=latency_ms,
            context_items_used=0,
            confidence_score=0.0,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_confidence(
        grounding: GroundingResult | None,
        hallucination: HallucinationReport | None,
        validation: ValidationResult | None,
    ) -> float:
        """
        Combine grounding confidence, hallucination risk, and validation
        outcome into a single 0.0-1.0 confidence score for the response.
        """
        base = grounding.overall_confidence if grounding else 0.5

        if hallucination is not None:
            from app.rag.hallucination_checker import HallucinationChecker
            risk = HallucinationChecker.compute_risk_score(hallucination)
            base = base * (1.0 - 0.5 * risk)

        if validation is not None and not validation.is_valid:
            base *= 0.7   # penalise responses that failed structural validation

        return max(0.0, min(1.0, base))